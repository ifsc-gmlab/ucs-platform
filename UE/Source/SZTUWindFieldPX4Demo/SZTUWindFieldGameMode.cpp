#include "SZTUWindFieldGameMode.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
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
#include "Components/TextRenderComponent.h"
#include "Engine/Canvas.h"
#include "Engine/GameViewportClient.h"
#include "Engine/TextureRenderTarget2D.h"
#include "UnrealClient.h"
#include "TimerManager.h"
#include "Styling/SlateBrush.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

void ASZTUDualCameraHUD::DrawHUD()
{
    Super::DrawHUD();
    if (!FParse::Param(FCommandLine::Get(), TEXT("DualCameraView")) || !Canvas)
    {
        return;
    }

    ASZTUDronePawn* Drone = nullptr;
    for (TActorIterator<ASZTUDronePawn> It(GetWorld()); It; ++It)
    {
        Drone = *It;
        break;
    }
    UTextureRenderTarget2D* RenderTarget = Drone ? Drone->GetCaptureRenderTarget() : nullptr;
    if (!RenderTarget)
    {
        return;
    }

    const float Margin = 22.0f;
    const float PanelWidth = Canvas->SizeX * 0.40f;
    const float ImageWidth = PanelWidth - 16.0f;
    const float ImageHeight = ImageWidth * 9.0f / 16.0f;
    const float PanelHeight = ImageHeight + 48.0f;
    const float X = Canvas->SizeX - PanelWidth - Margin;
    const float Y = Margin;
    DrawRect(FLinearColor(0.01f, 0.015f, 0.02f, 0.94f), X, Y, PanelWidth, PanelHeight);
    DrawText(TEXT("机载相机 / ONBOARD CAMERA"), FLinearColor::White,
        X + 10.0f, Y + 7.0f, nullptr, 1.0f, false);
    DrawTexture(RenderTarget, X + 8.0f, Y + 38.0f, ImageWidth, ImageHeight,
        0.0f, 0.0f, 1.0f, 1.0f, FLinearColor::White,
        BLEND_Opaque, 1.0f, false, 0.0f, FVector2D::ZeroVector);
}

ASZTUWindFieldGameMode::ASZTUWindFieldGameMode()
{
    if (FParse::Param(FCommandLine::Get(), TEXT("DualCameraView")))
    {
        HUDClass = ASZTUDualCameraHUD::StaticClass();
    }
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
    if (!GetWorld())
    {
        Super::StartPlay();
        return;
    }

    // The urban semantic map intentionally has no wind actor by default.
    // Inject one only for an explicit semantic-wind regression. Keep its
    // 3200-cell fan visualization hidden so it cannot obstruct the city map;
    // the actor still provides the same sampled wind model to the drone.
    float SemanticWindMps = 0.0f;
    if (FParse::Param(FCommandLine::Get(), TEXT("SemanticWind")) &&
        FParse::Value(FCommandLine::Get(), TEXT("WindMps="), SemanticWindMps))
    {
        ASZTUWindFieldActor* RuntimeWindField = nullptr;
        for (TActorIterator<ASZTUWindFieldActor> It(GetWorld()); It; ++It)
        {
            RuntimeWindField = *It;
            break;
        }
        if (!RuntimeWindField)
        {
            FActorSpawnParameters Params;
            Params.Name = TEXT("SZTU_SemanticRegressionWind");
            RuntimeWindField = GetWorld()->SpawnActor<ASZTUWindFieldActor>(
                ASZTUWindFieldActor::StaticClass(), FVector::ZeroVector,
                FRotator::ZeroRotator, Params);
        }
        if (RuntimeWindField)
        {
            RuntimeWindField->bShowFanArray = false;
            RuntimeWindField->bShowWindArrows = false;
            RuntimeWindField->SetActorHiddenInGame(true);
            RuntimeWindField->SetRuntimeWindSpeedMps(SemanticWindMps);
            UE_LOG(LogSZTUWindField, Display,
                TEXT("Semantic regression wind enabled: %.2f m/s (visualization hidden)"),
                SemanticWindMps);
        }
    }

    // Start player/pawn BeginPlay only after the optional runtime wind actor
    // exists, so ASZTUDronePawn resolves and caches the correct field.
    Super::StartPlay();

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

    bool bOnboardCameraActivated = false;
    if (FParse::Param(FCommandLine::Get(), TEXT("OnboardCamera")))
    {
        if (APlayerController* PlayerController = GetWorld()->GetFirstPlayerController())
        {
            for (TActorIterator<ASZTUDronePawn> It(GetWorld()); It; ++It)
            {
                if (It->OnboardCamera)
                {
                    It->OnboardCamera->SetActive(true);
                    PlayerController->SetViewTarget(*It);
                    bOnboardCameraActivated = true;
                    UE_LOG(LogSZTUWindField, Display, TEXT("Onboard camera active on DroneId=%d"), It->DroneId);
                    break;
                }
            }
        }
    }

    if (!bOnboardCameraActivated && bUseObserverCamera && !FParse::Param(FCommandLine::Get(), TEXT("NoObserverCamera")))
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
        else if (MapName.Contains(TEXT("SZTUFactoryFlightScene")))
        {
            CameraLocation = FVector(-350.0f, -1500.0f, 650.0f);
            CameraLookAt = FVector(0.0f, 150.0f, 110.0f);
            CameraFOV = 68.0f;
        }
        else if (MapName.Contains(TEXT("SZTULargeBridgeSemanticScene")))
        {
            if (FParse::Param(FCommandLine::Get(), TEXT("LogoReviewCamera")))
            {
                // Reproducible oblique close-up for checking transparent Logo
                // layers without starting PX4 or moving the aircraft.
                CameraLocation = FVector(2750.0f, -1050.0f, 520.0f);
                CameraLookAt = FVector(2750.0f, 0.0f, 18.0f);
                CameraFOV = 48.0f;
            }
            else
            {
                CameraLocation = FVector(2750.0f, -4300.0f, 1250.0f);
                CameraLookAt = FVector(2750.0f, 0.0f, 20.0f);
                CameraFOV = 70.0f;
            }
        }
        else if (MapName.Contains(TEXT("SZTUBridgeSemanticScene")))
        {
            // Operator overview: show both bridgeheads, the full arch profile,
            // the aircraft, and the water in one frame. The independent
            // VisionCapture component still records onboard inspection images.
            CameraLocation = FVector(1750.0f, -2700.0f, 850.0f);
            CameraLookAt = FVector(1750.0f, 0.0f, 40.0f);
            CameraFOV = 68.0f;
        }
        else if (MapName.Contains(TEXT("SZTUUrbanSemanticScene")))
        {
            // View the corridor from +Y toward -Y. The previous -Y camera made
            // the scene read backwards during review.
            CameraLocation = FVector(1100.0f, 1050.0f, 2450.0f);
            CameraLookAt = FVector(1150.0f, 0.0f, 80.0f);
            CameraFOV = 70.0f;
        }
                    else if (MapName.Contains(TEXT("SZTUFactoryDualScene")))
                    {
                        // 双机场景专用 v4：俯视相机 (0, -3, 6) m 看向 (0, 0, 0) m，
                        // FOV 80° 宽视角让两架（X=±1.5m）都装进视野。
                        CameraLocation = FVector(0.0f, -300.0f, 600.0f);
                        CameraLookAt = FVector(0.0f, 0.0f, 0.0f);
                        CameraFOV = 80.0f;
                    }
                    else if (MapName.Contains(TEXT("SZTUFactoryWindScene")))
                    {
                        // 中距侧斜俯视 v7：相机 (3, -6, 3.5) m 看向 (2.5, 0, 1.2) m，FOV 55°。
                        // 距离 6m 折中——飞机约 35 像素可见，旗子/集装箱在背景，扇墙扇阵不挡视野中央。
                        // v1/v8/v9/v10（远距 15m）都被风墙扇阵或集装箱挡视野；v7 是唯一能看到飞机的视角。
                        // 配合 UI 右上角 + 黄色机身 emissive 0.20 + 调暗扇叶 (0.05,0.18,0.20)，整体协调。
                        CameraLocation = FVector(300.0f, -600.0f, 350.0f);
                        CameraLookAt = FVector(250.0f, 0.0f, 120.0f);
                        CameraFOV = 55.0f;
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

                if (FParse::Param(FCommandLine::Get(), TEXT("FollowDroneCamera")))
                {
                    if (UCameraComponent* FollowCameraComponent = Camera->GetCameraComponent())
                    {
                        FollowCameraComponent->SetFieldOfView(55.0f);
                    }
                    ASZTUDronePawn* FollowDrone = nullptr;
                    for (TActorIterator<ASZTUDronePawn> It(GetWorld()); It; ++It)
                    {
                        FollowDrone = *It;
                        break;
                    }
                    if (FollowDrone)
                    {
                        if (FParse::Param(FCommandLine::Get(), TEXT("DualCameraView")) &&
                            FollowDrone->VisionCapture && FollowDrone->GetCaptureRenderTarget() &&
                            GEngine && GEngine->GameViewport)
                        {
                            // Render the onboard view every frame for the video
                            // overlay. Disk PNG readback remains at its configured
                            // low rate, so this does not add per-frame CPU encoding.
                            FollowDrone->VisionCapture->bCaptureEveryFrame = true;
                            FollowDrone->VisionCapture->bCaptureOnMovement = true;

                            UE_LOG(LogSZTUWindField, Display,
                                TEXT("Dual-camera HUD active (follow + onboard)."));
                        }

                        TWeakObjectPtr<ACameraActor> WeakCamera(Camera);
                        TWeakObjectPtr<ASZTUDronePawn> WeakDrone(FollowDrone);
                        FTimerHandle FollowCameraTimer;
                        GetWorldTimerManager().SetTimer(FollowCameraTimer,
                            [WeakCamera, WeakDrone]()
                            {
                                if (!WeakCamera.IsValid() || !WeakDrone.IsValid())
                                {
                                    return;
                                }
                                const FVector DroneLocation = WeakDrone->GetActorLocation();
                                if (DroneLocation.Z < 100.0f)
                                {
                                    // Before takeoff, use a raised staging view so
                                    // the bank/deck edge cannot occlude the vehicle.
                                    // Place the camera over the river-side shoulder,
                                    // not behind the near-bank slab.
                                    const FVector StagingLocation = DroneLocation + FVector(250.0f, -750.0f, 450.0f);
                                    const FVector StagingLookAt = DroneLocation + FVector(0.0f, 0.0f, 18.0f);
                                    WeakCamera->SetActorLocationAndRotation(
                                        StagingLocation, (StagingLookAt - StagingLocation).Rotation());
                                    return;
                                }
                                // Follow from the outside of the orbit while looking
                                // inward, keeping the aircraft in the foreground and
                                // the bridge in the background on every side.
                                const FVector BridgeCenter(2750.0f, 0.0f, 100.0f);
                                FVector Outward(
                                    DroneLocation.X - BridgeCenter.X,
                                    DroneLocation.Y - BridgeCenter.Y,
                                    0.0f);
                                if (!Outward.Normalize())
                                {
                                    Outward = FVector(0.0f, -1.0f, 0.0f);
                                }
                                const FVector FollowLocation = DroneLocation + Outward * 650.0f + FVector(0.0f, 0.0f, 350.0f);
                                const FVector LookAt = DroneLocation - Outward * 180.0f + FVector(0.0f, 0.0f, -20.0f);
                                constexpr float FollowUpdateSeconds = 0.02f;
                                const FVector SmoothedLocation = FMath::VInterpTo(
                                    WeakCamera->GetActorLocation(), FollowLocation,
                                    FollowUpdateSeconds, 3.2f);
                                const FRotator SmoothedRotation = FMath::RInterpTo(
                                    WeakCamera->GetActorRotation(),
                                    (LookAt - SmoothedLocation).Rotation(),
                                    FollowUpdateSeconds, 4.0f);
                                WeakCamera->SetActorLocationAndRotation(
                                    SmoothedLocation, SmoothedRotation);
                            }, 0.02f, true, 0.0f);
                        UE_LOG(LogSZTUWindField, Display,
                            TEXT("Third-person follow camera active on DroneId=%d"), FollowDrone->DroneId);
                    }
                }
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

    // Opt-in visual regression artifact. The delayed request runs after the
    // observer viewport has rendered, avoiding startup-time empty screenshots.
    const bool bLargeBridgeReviewShot = FParse::Param(FCommandLine::Get(), TEXT("LargeBridgeReviewShot"));
    if ((FParse::Param(FCommandLine::Get(), TEXT("BridgeReviewShot")) || bLargeBridgeReviewShot) && GetWorld())
    {
        FTimerHandle ReviewShotTimer;
        GetWorldTimerManager().SetTimer(ReviewShotTimer, [bLargeBridgeReviewShot]()
        {
            const FString ShotPath = FPaths::ProjectSavedDir() /
                (bLargeBridgeReviewShot
                    ? TEXT("Screenshots/Windows/LargeBridgeArchReview.png")
                    : TEXT("Screenshots/Windows/BridgeArchReview.png"));
            IFileManager::Get().MakeDirectory(*FPaths::GetPath(ShotPath), true);
            FScreenshotRequest::RequestScreenshot(ShotPath, false, false);
            UE_LOG(LogSZTUWindField, Display, TEXT("Bridge review screenshot requested: %s"), *ShotPath);
        }, 3.0f, false);
    }

    // Wind control UI:
    // - Wind scenes (factory-wind, demo) default to ON, suppress with -NoWindUI.
    // - Other scenes default to OFF, enable with -WindUI.
    // The on-screen text labels remain hidden; only the UMG slider shows.
    const FString WindUIMapName = GetWorld() ? GetWorld()->GetMapName() : FString();
    const bool bWindSceneByDefault =
        WindUIMapName.Contains(TEXT("WindScene")) ||
        WindUIMapName.Contains(TEXT("WindFieldDemoScene"));
    const bool bNoWindUIExplicit = FParse::Param(FCommandLine::Get(), TEXT("NoWindUI"));
    const bool bWindUIExplicit = FParse::Param(FCommandLine::Get(), TEXT("WindUI"));
    const bool bEnableWindUI = bWindSceneByDefault ? !bNoWindUIExplicit : bWindUIExplicit;
    if (bEnableWindUI)
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
                    // 左下角位置：绝不挡视野右上角 + 不被 DPI 缩放裁切
                    WindControlWidget->SetPositionInViewport(FVector2D(20.0f, 580.0f), false);

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

    // The generated maps contain legacy 3D labels that obstruct the vehicle
    // in the fixed observer view. Hide only world-space TextRender components;
    // the wind-speed UMG widget remains visible and interactive.
    int32 HiddenTextCount = 0;
    for (TActorIterator<AActor> It(GetWorld()); It; ++It)
    {
        TArray<UTextRenderComponent*> TextComponents;
        It->GetComponents<UTextRenderComponent>(TextComponents);
        for (UTextRenderComponent* TextComponent : TextComponents)
        {
            if (!TextComponent) { continue; }
            TextComponent->SetVisibility(false, true);
            TextComponent->SetHiddenInGame(true, true);
            ++HiddenTextCount;
        }
    }
    UE_LOG(LogSZTUWindField, Display,
        TEXT("Hidden %d world-space text component(s); viewport UI is unchanged"),
        HiddenTextCount);
}
