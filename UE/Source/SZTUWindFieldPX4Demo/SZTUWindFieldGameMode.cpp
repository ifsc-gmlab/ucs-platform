#include "SZTUWindFieldGameMode.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "SZTUDronePawn.h"
#include "SZTURoverPawn.h"
#include "SZTUWindFieldActor.h"
#include "SZTUWindControlWidget.h"
#include "SZTUWindFlag.h"
#include "SZTUWindFieldPX4Demo.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Components/StaticMeshComponent.h"

ASZTUWindFieldGameMode::ASZTUWindFieldGameMode()
{
    // Check -Vehicle=rover in constructor (before any pawn is spawned,
    // including the transition scene). This prevents a DronePawn from
    // being spawned first and grabbing TCP port 4560.
    FString VehicleType;
    if (FParse::Value(FCommandLine::Get(), TEXT("Vehicle="), VehicleType) &&
        VehicleType.Equals(TEXT("rover"), ESearchCase::IgnoreCase))
    {
        DefaultPawnClass = ASZTURoverPawn::StaticClass();
        UE_LOG(LogSZTUWindField, Display, TEXT("Constructor: -Vehicle=rover, using ASZTURoverPawn"));
    }
    else
    {
        DefaultPawnClass = ASZTUDronePawn::StaticClass();
    }
}

void ASZTUWindFieldGameMode::InitGame(const FString& MapName, const FString& Options, FString& ErrorMessage)
{
    // Set DefaultPawnClass BEFORE any pawn is spawned, based on the map name.
    // This avoids the destroy/respawn race that broke the rover's SITL connection.
    if (MapName.Contains(TEXT("Rover")))
    {
        DefaultPawnClass = ASZTURoverPawn::StaticClass();
        UE_LOG(LogSZTUWindField, Display, TEXT("InitGame: rover scene detected, using ASZTURoverPawn"));
    }
    else
    {
        DefaultPawnClass = ASZTUDronePawn::StaticClass();
    }
    Super::InitGame(MapName, Options, ErrorMessage);
}

void ASZTUWindFieldGameMode::StartPlay()
{
    Super::StartPlay();
    if (!GetWorld()) { return; }

    // 场景组合：默认不再强制注入风场/旗子，让"场景 / 旗子 / 无人机"各自独立，
    // 由关卡自行摆放。只有显式打开开关时才回退到“打开即自动填充”的便利行为。
    if (bAutoSpawnWindField)
    {
        bool bHasWindField = false;
        for (TActorIterator<ASZTUWindFieldActor> It(GetWorld()); It; ++It)
        {
            bHasWindField = true;
            break;
        }
        if (!bHasWindField)
        {
            FActorSpawnParameters Params;
            Params.Name = TEXT("SZTU_WindField_3200Fans");
            GetWorld()->SpawnActor<ASZTUWindFieldActor>(ASZTUWindFieldActor::StaticClass(), FVector::ZeroVector, FRotator::ZeroRotator, Params);
            UE_LOG(LogSZTUWindField, Display, TEXT("Auto-spawned wind field."));
        }
    }

    if (bAutoSpawnFlag)
    {
        bool bHasFlag = false;
        for (TActorIterator<ASZTUWindFlag> It(GetWorld()); It; ++It)
        {
            bHasFlag = true;
            break;
        }
        if (!bHasFlag)
        {
            FActorSpawnParameters Params;
            Params.Name = TEXT("SZTU_WindFlag");
            // 旗子几何体已重定向：法线朝向 X（风向），无需旋转
            GetWorld()->SpawnActor<ASZTUWindFlag>(ASZTUWindFlag::StaticClass(), FVector(200.0f, 0.0f, 0.0f), FRotator::ZeroRotator, Params);
            UE_LOG(LogSZTUWindField, Display, TEXT("Auto-spawned wind flag (rotated to face wind)."));
        }
    }

    if (bUseObserverCamera && !FParse::Param(FCommandLine::Get(), TEXT("NoObserverCamera")))
    {
        FVector CameraLocation = ObserverCameraLocation;
        FVector CameraLookAt = ObserverCameraLookAt;
        float CameraFOV = ObserverCameraFOV;
        const FString MapName = GetWorld()->GetMapName();
        if (MapName.Contains(TEXT("SZTUFlightPatternsScene")) || MapName.Contains(TEXT("SZTUHexacopterScene")))
        {
            CameraLocation = FVector(700.0f, -700.0f, 400.0f);
            CameraLookAt = FVector(250.0f, 0.0f, 100.0f);
            CameraFOV = 72.0f;
        }
        else if (MapName.Contains(TEXT("SZTURoverScene")))
        {
            CameraLocation = FVector(700.0f, -700.0f, 260.0f);
            CameraLookAt = FVector(250.0f, 0.0f, 35.0f);
            CameraFOV = 72.0f;
        }

        FActorSpawnParameters CameraParams;
        CameraParams.Name = TEXT("SZTU_ObserverCamera");
        CameraParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

        const FRotator CameraRotation = (CameraLookAt - CameraLocation).Rotation();
        ACameraActor* Camera = GetWorld()->SpawnActor<ACameraActor>(
            ACameraActor::StaticClass(),
            CameraLocation,
            CameraRotation,
            CameraParams);

        if (Camera)
        {
            if (UCameraComponent* CameraComponent = Camera->GetCameraComponent())
            {
                CameraComponent->SetFieldOfView(CameraFOV);
            }

            if (APlayerController* PlayerController = GetWorld()->GetFirstPlayerController())
            {
                PlayerController->SetViewTarget(Camera);
                UE_LOG(LogSZTUWindField, Display,
                    TEXT("Observer camera active: loc=%s lookAt=%s fov=%.1f"),
                    *CameraLocation.ToString(),
                    *CameraLookAt.ToString(),
                    CameraFOV);
            }
            else
            {
                UE_LOG(LogSZTUWindField, Warning, TEXT("Observer camera spawned but no PlayerController was available."));
            }
        }
        else
        {
            UE_LOG(LogSZTUWindField, Warning, TEXT("Failed to spawn observer camera."));
        }
    }
    else
    {
        UE_LOG(LogSZTUWindField, Display, TEXT("Observer camera disabled."));
    }

    // Wind control UI disabled — scene runs clean without on-screen text.
    // Re-enable with -WindUI command line flag if needed.
    if (FParse::Param(FCommandLine::Get(), TEXT("WindUI")))
    {
        ASZTUWindFieldActor* RuntimeWindField = nullptr;
        for (TActorIterator<ASZTUWindFieldActor> It(GetWorld()); It; ++It)
        {
            RuntimeWindField = *It;
            break;
        }

        if (RuntimeWindField)
        {
            if (APlayerController* PlayerController = GetWorld()->GetFirstPlayerController())
            {
                WindControlWidget = CreateWidget<USZTUWindControlWidget>(PlayerController, USZTUWindControlWidget::StaticClass());
                if (WindControlWidget)
                {
                    WindControlWidget->ConfigureWindField(RuntimeWindField);
                    WindControlWidget->AddToViewport(100);
                    WindControlWidget->SetDesiredSizeInViewport(FVector2D(420.0f, 130.0f));
                    WindControlWidget->SetPositionInViewport(FVector2D(24.0f, 24.0f), false);

                    FInputModeGameAndUI InputMode;
                    InputMode.SetHideCursorDuringCapture(false);
                    PlayerController->SetInputMode(InputMode);
                    PlayerController->SetShowMouseCursor(true);
                    UE_LOG(LogSZTUWindField, Display, TEXT("Wind control UI active: %.2f m/s"), RuntimeWindField->GetRuntimeWindSpeedMps());
                }
            }
        }
    }

    // Ground color override: -GroundColor=R,G,B (0-255) or -DarkGround for default dark gray
    FString GroundColorStr;
    bool bUseDarkGround = FParse::Param(FCommandLine::Get(), TEXT("DarkGround"));
    if (bUseDarkGround || FParse::Value(FCommandLine::Get(), TEXT("GroundColor="), GroundColorStr))
    {
        FLinearColor NewColor(0.165f, 0.165f, 0.18f, 1.0f); // default dark blue-gray #2A2A2E
        if (!bUseDarkGround)
        {
            TArray<FString> Parts;
            GroundColorStr.ParseIntoArray(Parts, TEXT(","), true);
            if (Parts.Num() >= 3)
            {
                NewColor = FLinearColor(
                    FCString::Atof(*Parts[0]) / 255.0f,
                    FCString::Atof(*Parts[1]) / 255.0f,
                    FCString::Atof(*Parts[2]) / 255.0f, 1.0f);
            }
        }

        // REPLACE ground materials entirely with M_GroundColor (which has
        // a known BaseColor parameter). The original ground material's color
        // is likely baked into textures and has no runtime parameter.
        UMaterialInterface* OverrideMat = LoadObject<UMaterialInterface>(nullptr,
            TEXT("/Game/Drone/Materials/M_GroundColor.M_GroundColor"));
        if (!OverrideMat)
        {
            UE_LOG(LogSZTUWindField, Warning, TEXT("M_GroundColor not found, cannot override ground"));
        }
        UMaterialInstanceDynamic* OverrideMID = OverrideMat
            ? UMaterialInstanceDynamic::Create(OverrideMat, nullptr) : nullptr;
        if (OverrideMID)
        {
            OverrideMID->SetVectorParameterValue(TEXT("BaseColor"), NewColor);
        }

        int32 OverrideCount = 0;
        for (TActorIterator<AActor> It(GetWorld()); It; ++It)
        {
            AActor* Actor = *It;
            if (!Actor) continue;

            // Static mesh components
            TArray<UStaticMeshComponent*> SMComps;
            Actor->GetComponents<UStaticMeshComponent>(SMComps);
            for (UStaticMeshComponent* SM : SMComps)
            {
                FString Name = SM->GetName().ToLower();
                if (Name.Contains(TEXT("ground")) || Name.Contains(TEXT("floor")) ||
                    Name.Contains(TEXT("plane")) || Name.Contains(TEXT("terrain")))
                {
                    if (OverrideMID)
                    {
                        for (int32 SlotIdx = 0; SlotIdx < SM->GetNumMaterials(); ++SlotIdx)
                        {
                            SM->SetMaterial(SlotIdx, OverrideMID);
                        }
                    }
                    OverrideCount++;
                }
            }

            // Skip non-ground actors
        }

        UE_LOG(LogSZTUWindField, Display,
            TEXT("Ground color override: RGB(%.0f,%.0f,%.0f) applied to %d component(s)"),
            NewColor.R * 255, NewColor.G * 255, NewColor.B * 255, OverrideCount);
    }
}
