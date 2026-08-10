#include "SZTUDronePawn.h"
#include "Components/StaticMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Camera/CameraComponent.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "UnrealClient.h"
#include "IImageWrapperModule.h"
#include "IImageWrapper.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Components/InputComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Dom/JsonObject.h"
#include "GameFramework/PlayerController.h"
#include "IPAddress.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "Common/UdpSocketBuilder.h"
#include "UObject/ConstructorHelpers.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/Material.h"
#include "DrawDebugHelpers.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "SZTUWindFieldPX4Demo.h"
#include "SZTUMavlinkSITLComponent.h"
#include "SZTUFlightControllerComponent.h"

ASZTUDronePawn::ASZTUDronePawn()
{
    PrimaryActorTick.bCanEverTick = true;

    // Iris 机身（已导入的模型）
    Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
    RootComponent = Body;
    Body->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    Body->SetSimulatePhysics(false);

    OnboardCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("OnboardCamera"));
    OnboardCamera->SetupAttachment(Body);
    OnboardCamera->SetRelativeLocation(FVector(28.0f, 0.0f, 10.0f));
    OnboardCamera->SetRelativeRotation(FRotator(-6.0f, 0.0f, 0.0f));
    OnboardCamera->SetFieldOfView(92.0f);
    OnboardCamera->PostProcessSettings.bOverride_AutoExposureBias = true;
    OnboardCamera->PostProcessSettings.AutoExposureBias = 2.0f;
    OnboardCamera->PostProcessBlendWeight = 1.0f;
    OnboardCamera->SetAutoActivate(false);

    // Vision capture camera (same pose as onboard camera, renders to texture)
    VisionCapture = CreateDefaultSubobject<USceneCaptureComponent2D>(TEXT("VisionCapture"));
    VisionCapture->SetupAttachment(Body);
    VisionCapture->SetRelativeLocation(FVector(28.0f, 0.0f, 10.0f));
    VisionCapture->SetRelativeRotation(FRotator(-6.0f, 0.0f, 0.0f));
    VisionCapture->FOVAngle = 92.0f;
    // Save the same tonemapped LDR color the player viewport sees. The default
    // HDR scene-color capture was written straight into an 8-bit PNG, making
    // the dashboard preview look almost completely black.
    VisionCapture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    VisionCapture->PostProcessSettings.bOverride_AutoExposureBias = true;
    VisionCapture->PostProcessSettings.AutoExposureBias = 2.0f;
    VisionCapture->PostProcessBlendWeight = 1.0f;
    // Manual one-Hz captures otherwise discard temporal exposure history and
    // repeatedly produce a near-black first frame.
    VisionCapture->bAlwaysPersistRenderingState = true;
    VisionCapture->bCaptureEveryFrame = false;
    VisionCapture->bCaptureOnMovement = false;

    // Iris 机身（OBJ 导入的网格）
    static ConstructorHelpers::FObjectFinder<UStaticMesh> IrisMesh(TEXT("/Game/Drone/Meshes/iris.iris"));
    static ConstructorHelpers::FObjectFinder<UStaticMesh> CylBody(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    UStaticMesh* BodyMesh = nullptr;

    if (IrisMesh.Succeeded())
    {
        BodyMesh = IrisMesh.Object;
        Body->SetRelativeScale3D(FVector(1.0f, 1.0f, 1.0f));  // OBJ 已预缩放到真实尺寸，无需再放大
        UE_LOG(LogSZTUWindField, Display, TEXT("Iris mesh loaded: %s, materials=%d"),
            *IrisMesh.Object->GetFullName(), IrisMesh.Object->GetStaticMaterials().Num());
    }
    else if (CylBody.Succeeded())
    {
        BodyMesh = CylBody.Object;
        Body->SetRelativeScale3D(FVector(0.5f, 0.5f, 0.2f));
        UE_LOG(LogSZTUWindField, Warning, TEXT("Iris mesh failed, using cylinder body"));
    }

    if (BodyMesh)
    {
        Body->SetStaticMesh(BodyMesh);
    }
    // 优先使用 commandlet 生成的新颜色材质（M_DroneBodyColor，带 BaseColor 参数）
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> ColorBodyMatFinder(TEXT("/Game/Drone/Materials/M_DroneBodyColor.M_DroneBodyColor"));
    if (ColorBodyMatFinder.Succeeded())
    {
        const int32 NumMats = Body->GetNumMaterials();
        for (int32 i = 0; i < NumMats; ++i)
        {
            Body->SetMaterial(i, ColorBodyMatFinder.Object);
        }
        UE_LOG(LogSZTUWindField, Display, TEXT("Body material set to M_DroneBodyColor (orange), slots=%d"), NumMats);
    }
    else
    {
        static ConstructorHelpers::FObjectFinder<UMaterialInterface> BodyMatFinder(TEXT("/Game/Drone/Materials/MI_DroneBody.MI_DroneBody"));
        if (BodyMatFinder.Succeeded())
        {
            Body->SetMaterial(0, BodyMatFinder.Object);
            UE_LOG(LogSZTUWindField, Display, TEXT("Body material set to MI_DroneBody (fallback)"));
        }
    }

    // 4 个旋翼（桨叶 DAE 没导入则用 Cylinder 薄片，但调大比例使其视觉上像桨盘）
    static ConstructorHelpers::FObjectFinder<UStaticMesh> CylinderMesh(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    static ConstructorHelpers::FObjectFinder<UStaticMesh> PropCWMesh(TEXT("/Game/Drone/Meshes/iris_prop_cw.iris_prop_cw"));
    static ConstructorHelpers::FObjectFinder<UStaticMesh> PropCCWMesh(TEXT("/Game/Drone/Meshes/iris_prop_ccw.iris_prop_ccw"));

    UStaticMesh* PropCW = PropCWMesh.Succeeded()   ? PropCWMesh.Object  : (CylinderMesh.Succeeded() ? CylinderMesh.Object : nullptr);
    UStaticMesh* PropCCW = PropCCWMesh.Succeeded()  ? PropCCWMesh.Object : (CylinderMesh.Succeeded() ? CylinderMesh.Object : nullptr);

    // 旋翼偏移：X/Y 独立，适配 iris 非对称机臂（X≈14cm, Y≈23cm，电机座实测）
    const float OX = RotorOffsetX;
    const float OY = RotorOffsetY;
    const FVector RotorOffsets[4] = {
        FVector( OX, -OY, 0.0f),
        FVector( OX,  OY, 0.0f),
        FVector(-OX,  OY, 0.0f),
        FVector(-OX, -OY, 0.0f)
    };
    UStaticMesh* PropMeshes[4] = { PropCW, PropCCW, PropCW, PropCCW };

    for (int32 i = 0; i < 4; ++i)
    {
        FString RotorName = FString::Printf(TEXT("Rotor_%d"), i);
        UStaticMeshComponent* Rotor = CreateDefaultSubobject<UStaticMeshComponent>(*RotorName);
        Rotor->SetupAttachment(Body);
        Rotor->SetStaticMesh(PropMeshes[i]);
        Rotor->SetRelativeLocation(RotorOffsets[i]);
        bool bIsCylinderFallback = (PropMeshes[i] == CylinderMesh.Object);
        // 使用 Cylinder 兜底时：宽而扁的桨盘（0.35半径×0.035高），比之前更显眼
        // 真实桨叶：在导入缩放基础上再乘 RotorScaleMultiplier
        const float S = RotorScaleMultiplier;
        Rotor->SetRelativeScale3D(bIsCylinderFallback ? FVector(0.35f * S, 0.35f * S, 0.035f * S) : FVector(S, S, S));
        Rotor->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        // 在 BeginPlay 中由 ApplyMaterialsAtRuntime 统一设置材质
        RotorDiscs.Add(Rotor);
    }

    // 桨叶：在构造函数也设 M_DronePropellerColor，保证编辑器视口可见（非仅 PIE）
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> PropMatBase(TEXT("/Game/Drone/Materials/M_DronePropellerColor.M_DronePropellerColor"));
    if (PropMatBase.Succeeded())
    {
        for (UStaticMeshComponent* Rotor : RotorDiscs)
        {
            const int32 NumMats = Rotor->GetNumMaterials();
            for (int32 i = 0; i < NumMats; ++i)
            {
                Rotor->SetMaterial(i, PropMatBase.Object);
            }
        }
        UE_LOG(LogSZTUWindField, Display, TEXT("Propeller material set to M_DronePropellerColor (dark gray), rotors=%d"), RotorDiscs.Num());
    }

    EnvelopeMonitor = CreateDefaultSubobject<USZTUEnvelopeMonitorComponent>(TEXT("EnvelopeMonitor"));
    SITLComponent = CreateDefaultSubobject<USZTUMavlinkSITLComponent>(TEXT("SITLComponent"));
    AutoPossessPlayer = EAutoReceiveInput::Player0;
}

void ASZTUDronePawn::ApplyMaterialsAtRuntime()
{
    // 输出机身网格诊断信息
    if (Body && Body->GetStaticMesh())
    {
        UStaticMesh* SM = Body->GetStaticMesh();
        FBoxSphereBounds Bounds = SM->GetBounds();
        const int32 NumMats = Body->GetNumMaterials();
        UE_LOG(LogSZTUWindField, Display, TEXT("Body mesh: %s, bounds=(%.2f,%.2f,%.2f) radius=%.1f mats=%d"),
            *SM->GetName(), Bounds.BoxExtent.X, Bounds.BoxExtent.Y, Bounds.BoxExtent.Z, Bounds.SphereRadius, NumMats);
    }

    // 机身：加载 commandlet 创建的材质资产，用 DMID 动态设色（遍历所有 material slots）
    UMaterialInterface* BodyMatBase = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Drone/Materials/M_DroneBodyColor.M_DroneBodyColor"));
    if (Body && BodyMatBase)
    {
        UMaterialInstanceDynamic* BodyDMID = UMaterialInstanceDynamic::Create(BodyMatBase, this);
        if (BodyDMID)
        {
            BodyDMID->SetVectorParameterValue(TEXT("BaseColor"), BodyColor);
            const int32 NumMats = Body->GetNumMaterials();
            for (int32 i = 0; i < NumMats; ++i)
            {
                Body->SetMaterial(i, BodyDMID);
            }
            UE_LOG(LogSZTUWindField, Display, TEXT("Body material: M_DroneBodyColor via DMID, slots=%d, R=%.2f G=%.2f B=%.2f"),
                NumMats, BodyColor.R, BodyColor.G, BodyColor.B);
        }
    }
    else
    {
        UE_LOG(LogSZTUWindField, Warning, TEXT("ApplyMaterials: M_DroneBodyColor not found or Body missing"));
    }

    // 桨叶：加载 commandlet 创建的材质资产，用 DMID 动态设色（深灰）
    UMaterialInterface* PropMatBase = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Drone/Materials/M_DronePropellerColor.M_DronePropellerColor"));
    if (PropMatBase)
    {
        UMaterialInstanceDynamic* PropDMID = UMaterialInstanceDynamic::Create(PropMatBase, this);
        if (PropDMID)
        {
            PropDMID->SetVectorParameterValue(TEXT("BaseColor"), PropellerColor);
            for (UStaticMeshComponent* Rotor : RotorDiscs)
            {
                const int32 NumMats = Rotor->GetNumMaterials();
                for (int32 i = 0; i < NumMats; ++i)
                {
                    Rotor->SetMaterial(i, PropDMID);
                }
                if (Rotor->GetStaticMesh())
                {
                    FBoxSphereBounds RBounds = Rotor->GetStaticMesh()->GetBounds();
                    UE_LOG(LogSZTUWindField, Display, TEXT("Rotor mesh: %s, bounds=(%.3f,%.3f,%.3f) radius=%.2f mats=%d"),
                        *Rotor->GetStaticMesh()->GetName(),
                        RBounds.BoxExtent.X, RBounds.BoxExtent.Y, RBounds.BoxExtent.Z, RBounds.SphereRadius, NumMats);
                }
            }
            UE_LOG(LogSZTUWindField, Display, TEXT("Propeller material: M_DronePropellerColor via DMID, R=%.2f G=%.2f B=%.2f, rotors=%d"),
                PropellerColor.R, PropellerColor.G, PropellerColor.B, RotorDiscs.Num());
        }
    }
    else
    {
        UE_LOG(LogSZTUWindField, Warning, TEXT("ApplyMaterials: M_DronePropellerColor not found"));
    }

    UE_LOG(LogSZTUWindField, Display, TEXT("Runtime materials applied: rotors=%d, body=%s"),
        RotorDiscs.Num(), Body && Body->GetStaticMesh() ? TEXT("mesh assigned") : TEXT("no mesh"));
}

void ASZTUDronePawn::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);

    // 编辑器中属性变化时实时更新旋翼位置
    const float OX = RotorOffsetX;
    const float OY = RotorOffsetY;
    const FVector RotorOffsets[4] = {
        FVector( OX, -OY, 0.0f),
        FVector( OX,  OY, 0.0f),
        FVector(-OX,  OY, 0.0f),
        FVector(-OX, -OY, 0.0f)
    };
    for (int32 i = 0; i < RotorDiscs.Num() && i < 4; ++i)
    {
        if (RotorDiscs[i])
        {
            RotorDiscs[i]->SetRelativeLocation(RotorOffsets[i]);
        }
    }
}

void ASZTUDronePawn::BeginPlay()
{
    Super::BeginPlay();
    LocateOrSpawnWindField();

    FString TelemetryHostOverride;
    if (FParse::Value(FCommandLine::Get(), TEXT("TelemetryHost="), TelemetryHostOverride) &&
        !TelemetryHostOverride.IsEmpty())
    {
        TelemetryHost = TelemetryHostOverride;
    }
    int32 TelemetryPortOverride = 0;
    if (FParse::Value(FCommandLine::Get(), TEXT("TelemetryPort="), TelemetryPortOverride) &&
        TelemetryPortOverride > 0 && TelemetryPortOverride <= 65535)
    {
        TelemetryPort = TelemetryPortOverride;
    }
    OpenTelemetrySocket();
    SetupVisionCapture();  // Must be after world is available for map-name check

    FString RequestedVehicle;
    if (FParse::Value(FCommandLine::Get(), TEXT("Vehicle="), RequestedVehicle) &&
        RequestedVehicle.Equals(TEXT("hex"), ESearchCase::IgnoreCase))
    {
        Airframe = ESZTUMultirotorAirframe::HexaX;
    }

    const bool bHexaX = Airframe == ESZTUMultirotorAirframe::HexaX;

    // 运行时加载机身网格（解决 CDO 在导入前已构造的问题）
    UStaticMesh* IrisMesh = LoadObject<UStaticMesh>(nullptr, bHexaX
        ? TEXT("/Game/Vehicles/Hexacopter/Meshes/typhoon_h480_complete.typhoon_h480_complete")
        : TEXT("/Game/Drone/Meshes/iris.iris"));
    if (IrisMesh)
    {
        Body->SetStaticMesh(IrisMesh);
        Body->SetRelativeScale3D(FVector(1.0f));
        Body->SetRelativeLocation(bHexaX ? FVector(0.0f, 0.0f, 8.4f) : FVector::ZeroVector);
        Body->SetVisibility(true, true);
        Body->SetHiddenInGame(false);
        UE_LOG(LogSZTUWindField, Display, TEXT("BeginPlay: loaded %s airframe mesh"), bHexaX ? TEXT("Hexa-X") : TEXT("Quad-X"));
    }

    // Color by airframe AND scene:
    // - HexaX = blue (used in SZTUHexacopterScene)
    // - Quad in factory-wind scene = bright white, contrasts strongly with the
    //   dark asphalt/factory floor (orange drone was hard to see against the
    //   brownish factory background).
    // - Quad elsewhere = orange (default).
    const FString CurrentMapName = GetWorld() ? GetWorld()->GetMapName() : FString();
    const bool bFactoryWindScene = CurrentMapName.Contains(TEXT("FactoryWindScene"));
    const bool bFactoryDualScene = CurrentMapName.Contains(TEXT("FactoryDualScene"));
    const bool bSemanticScene = CurrentMapName.Contains(TEXT("SemanticScene"));
    if (bSemanticScene)
    {
        bEnableProximitySensors = true;
        bEnableHorizontalCollisionSweep = true;
    }
    bShowSensorRays = FParse::Param(FCommandLine::Get(), TEXT("ShowSensorRays"));
    if (OnboardCamera)
    {
        OnboardCamera->SetActive(FParse::Param(FCommandLine::Get(), TEXT("OnboardCamera")));
    }
    FLinearColor VehicleColor;
    if (bHexaX)
    {
        VehicleColor = FLinearColor(0.12f, 0.45f, 0.85f, 1.0f);  // blue #1F73CC
    }
    else if (bFactoryDualScene)
    {
        // 双机场景：按 DroneId 区分颜色（1=黄、2=亮青）方便用户在画面里分辨
        if (DroneId == 2)
        {
            VehicleColor = FLinearColor(0.20f, 0.95f, 0.95f, 1.0f);  // bright cyan #33F2F2
        }
        else
        {
            VehicleColor = FLinearColor(1.0f, 0.90f, 0.0f, 1.0f);  // bright yellow #FFE600
        }
    }
    else if (bFactoryWindScene)
    {
        VehicleColor = FLinearColor(1.0f, 0.90f, 0.0f, 1.0f);  // bright yellow #FFE600
    }
    else
    {
        VehicleColor = FLinearColor(0.85f, 0.45f, 0.10f, 1.0f);  // orange #D9731A
    }
    // Sync BodyColor so ApplyMaterialsAtRuntime() (called below) uses the
    // scene-specific color. Without this, the orange default from the
    // UPROPERTY would override our scene-aware choice.
    BodyColor = VehicleColor;
    if (UMaterialInterface* OverrideMat = LoadObject<UMaterialInterface>(nullptr,
        TEXT("/Game/Drone/Materials/M_DroneBodyColor.M_DroneBodyColor")))
    {
        if (UMaterialInstanceDynamic* OverrideMID = UMaterialInstanceDynamic::Create(OverrideMat, Body))
        {
            OverrideMID->SetVectorParameterValue(TEXT("BaseColor"), VehicleColor);
            for (int32 i = 0; i < Body->GetNumMaterials(); i++)
            {
                Body->SetMaterial(i, OverrideMID);
            }
        }
    }

    if (bHexaX)
    {
        DroneMassKg = 2.66f;
        MaxMotorThrustN = 19.23f;
        AngularDamping = 2.5f;
    }
    for (UStaticMeshComponent* Rotor : RotorDiscs)
    {
        Rotor->SetVisibility(!bHexaX, true);
        Rotor->SetHiddenInGame(bHexaX);
    }
    if (SITLComponent)
    {
        SITLComponent->SetActuatorCount(bHexaX ? 6 : 4);
        // 双机并行：按 DroneId 设 TCP 端口（DroneId=1→4560, DroneId=2→4561）
        // 单机场景默认 DroneId=1 跟旧版完全一致。
        SITLComponent->SetTransportPort(4559 + DroneId);
        // SITLComponent BeginPlay 已经用旧 ListenPort=4560 bind 过了，
        // 改完 ListenPort 必须重新 bind，否则 DroneId=2 仍然 listen 4560。
        SITLComponent->ReinitializeTransport();
        UE_LOG(LogSZTUWindField, Display, TEXT("DronePawn id=%d -> controller=%s transport port=%d"),
            DroneId, *SITLComponent->GetBackendName().ToString(), SITLComponent->GetTransportPort());
    }
    ApplyMaterialsAtRuntime();

    // 生成在风场前方方便观察。关键：Z 必须≈0（贴地），不能在空中！
    // 原因：PX4 连接(SITL 物理接管)后无人机才受重力；若生成在 1.2m 高处，
    // 连接瞬间会自由落体 ~0.5s，落体期间 IMU 比力=(0,0,0)、落地后=(0,0,9.81)，
    // EKF2 加速度计偏置估计器会把"本该是重力、却读成 0"误判成巨大偏置(~-9.81)，
    // 永久触发 "High Accelerometer Bias" + "vertical velocity unstable"，且重力/偏置一旦锁定无法自恢复。
    // 贴地生成则连接瞬间即 bGrounded=true、IMU 恒为 (0,0,9.81)，偏置直接收敛到 0。
    // 双机并行：DroneId=1 → X=250（跟旧版单机一致，相机 LookAt 对准）
    // DroneId=2 → X=-250（错开 5m 避免重叠），DroneId=3+ → X=-750,-1250,... 依次类推
    // 2026-07-21：双机场景相机 FOV 80° 俯视 + LookAt=(0,0,0)
    // DroneId=1 位置 (1.0, 0, 0)，DroneId=2 位置 (-1.0, 3.0, 0) 偏 +Y 3m 避开风墙扇阵遮挡
    const FString CurrentMapNameForSpawn = GetWorld() ? GetWorld()->GetMapName() : FString();
    const bool bDualScene = CurrentMapNameForSpawn.Contains(TEXT("FactoryDualScene"));
    FVector SpawnLocation;
    if (bDualScene)
    {
        SpawnLocation = (DroneId == 1) ? FVector(100.0f, 0.0f, 0.0f) : FVector(-100.0f, 300.0f, 0.0f);
    }
    else
    {
        SpawnLocation = (DroneId == 1) ? FVector(250.0f, 0.0f, 0.0f) : FVector(250.0f - (DroneId - 1) * 500.0f, 0.0f, 0.0f);
    }
    SetActorLocation(SpawnLocation);
    SemanticRouteOriginWorld = SpawnLocation;
    SemanticRoutePreviewPath = FPaths::ProjectSavedDir() / TEXT("semantic_route_preview.json");
    RefreshSemanticRoutePreview(0.0f, true);
    UE_LOG(LogSZTUWindField, Display, TEXT("Drone spawned at %s (DroneId=%d)"), *GetActorLocation().ToString(), DroneId);
}

void ASZTUDronePawn::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    CloseTelemetrySocket();
    Super::EndPlay(EndPlayReason);
}

void ASZTUDronePawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);
    PlayerInputComponent->BindAxis(TEXT("Throttle"), this, &ASZTUDronePawn::SetThrottle);
    PlayerInputComponent->BindAxis(TEXT("Pitch"), this, &ASZTUDronePawn::SetPitch);
    PlayerInputComponent->BindAxis(TEXT("Roll"), this, &ASZTUDronePawn::SetRoll);
    PlayerInputComponent->BindAxis(TEXT("Yaw"), this, &ASZTUDronePawn::SetYaw);
}

void ASZTUDronePawn::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (DeltaSeconds <= KINDA_SMALL_NUMBER) { return; }

    RefreshSemanticRoutePreview(DeltaSeconds);
    DrawSemanticRoutePreview();

    if (!WindField && !bWindFieldLookupComplete) { LocateOrSpawnWindField(); }

    const FSZTUWindSample WindSample = WindField ? WindField->SampleWindAtWorldLocation(GetActorLocation()) : FSZTUWindSample();

    // 电机几何必须与 PX4 的 quad_x 控制分配矩阵严格一致，否则 PX4 对“某电机推力产生的姿态力矩方向”
    // 的假设与 UE 实际施加的力矩方向相反 → 三轴速率闭环全反 → 翻滚、不离地。
    // 推导（FRD，推力轴=机体 -Z 即“向上”）：
    //   rotor_i 力矩 τ_i = r_i × (T_i·(0,0,-1)) = (-PY_i·T_i, +PX_i·T_i, 0) + (0,0, KM_i·T_i)
    //   UE 在 FRU 施加 τ_fru = (ΣT·MotorPY, -ΣT·MotorPX, ΣT·MotorKM)，转 FRD(轴向量 -C) 得
    //   τ_frd = (-ΣT·MotorPY, +ΣT·MotorPX, +ΣT·MotorKM)。令其逐项等于 PX4 期望 ⇒
    //   MotorPX[i]=PX4_PX[i], MotorPY[i]=PX4_PY[i], MotorKM[i]=PX4_KM[i]（无需任何额外符号翻转）。
    // 以下为 10016_none_iris 的 CA_ROTOR0..3 实测值（米，机体 X前Y右Z下）：
    //   ROTOR0: PX=+0.1515  PY=+0.245   KM=+0.05   (FR)
    //   ROTOR1: PX=-0.1515  PY=-0.1875  KM=+0.05   (BL)
    //   ROTOR2: PX=+0.1515  PY=-0.245   KM=-0.05   (FL)
    //   ROTOR3: PX=-0.1515  PY=+0.1875  KM=-0.05   (BR)
    static const float QuadMotorPX[6] = {  0.1515f, -0.1515f,  0.1515f, -0.1515f, 0.0f, 0.0f };
    static const float QuadMotorPY[6] = {  0.2450f, -0.1875f, -0.2450f,  0.1875f, 0.0f, 0.0f };
    static const float QuadMotorKM[6] = {  0.05f,    0.05f,   -0.05f,   -0.05f, 0.0f, 0.0f };
    // PX4 airframe 6001 (Generic Hexarotor X), scaled to the Typhoon model.
    static const float HexMotorPX[6] = { 0.0f, 0.0f, 0.258f, -0.258f, 0.258f, -0.258f };
    static const float HexMotorPY[6] = { 0.3f, -0.3f, -0.15f, 0.15f, 0.15f, -0.15f };
    static const float HexMotorKM[6] = { -0.06f, 0.06f, -0.06f, 0.06f, 0.06f, -0.06f };
    const bool bHexaX = Airframe == ESZTUMultirotorAirframe::HexaX;
    const int32 RotorCount = bHexaX ? 6 : 4;
    const float* MotorPX = bHexaX ? HexMotorPX : QuadMotorPX;
    const float* MotorPY = bHexaX ? HexMotorPY : QuadMotorPY;
    const float* MotorKM = bHexaX ? HexMotorKM : QuadMotorKM;

    // 获取 PX4 电机推力
    TArray<float> MotorThrusts;
    bool bHasSITL = false;
    if (SITLComponent)
    {
        SITLComponent->SetActuatorCount(RotorCount);
        SITLComponent->GetMotorThrusts(MotorThrusts);
        bHasSITL = SITLComponent->IsConnected() && MotorThrusts.Num() >= RotorCount;
    }

    FQuat AttitudeQuat = GetActorQuat();
    FVector AccelMps2 = FVector::ZeroVector;
    float TotalThrustN = 0.f;

    float CommandedThrustN = 0.f;
    if (bHasSITL)
    {
        for (int32 i = 0; i < RotorCount; ++i)
        {
            const float MotorCommand = FMath::Clamp(MotorThrusts[i], 0.f, 1.f);
            CommandedThrustN += MotorCommand * MotorCommand * MaxMotorThrustN;
        }
    }

    const bool bManualInputActive =
        FMath::Abs(ThrottleInput) > 0.01f ||
        FMath::Abs(PitchInput) > 0.01f ||
        FMath::Abs(RollInput) > 0.01f ||
        FMath::Abs(YawInput) > 0.01f;
    const FVector CurrentLocation = GetActorLocation();
    const bool bNearGround = CurrentLocation.Z <= 2.0f;
    const float VehicleWeightN = DroneMassKg * 9.81f;
    const bool bLiftCommanded = CommandedThrustN >= VehicleWeightN * 0.98f;
    const bool bStrongLiftCommanded = CommandedThrustN >= VehicleWeightN * 1.10f;
    const bool bAirborne = CurrentLocation.Z > 5.0f;

    if (!bTouchdownLatched && bNearGround && VelocityMps.Z <= 0.05f)
    {
        bTouchdownLatched = true;
        GroundReleaseTimerSec = 0.0f;
    }
    if (bTouchdownLatched)
    {
        GroundReleaseTimerSec = bStrongLiftCommanded
            ? GroundReleaseTimerSec + DeltaSeconds : 0.0f;
        if (GroundReleaseTimerSec >= 0.20f)
        {
            bTouchdownLatched = false;
            GroundReleaseTimerSec = 0.0f;
        }
    }
    const bool bGroundLocked =
        bTouchdownLatched ||
        (bNearGround &&
         ((!bHasSITL && !bManualInputActive) || (bHasSITL && !bLiftCommanded && !bAirborne)));

    const FVector WindAccel = bGroundLocked ? FVector::ZeroVector : (WindSample.VelocityMps - VelocityMps) * WindDragGain;

    if (bGroundLocked)
    {
        // Keep the visual motor state live while armed on the ground, then let
        // it decay naturally after DISARM instead of freezing the propellers.
        if (bHasSITL)
        {
            const float MotorAlpha = 1.0f - FMath::Exp(-DeltaSeconds / MotorTau);
            const int32 VisualRotorCount = FMath::Min(RotorCount, RotorDiscs.Num());
            for (int32 i = 0; i < RotorCount; ++i)
            {
                const float TargetRPM = FMath::Clamp(MotorThrusts[i], 0.f, 1.f) * MaxRPM;
                ActualRPM[i] += (TargetRPM - ActualRPM[i]) * MotorAlpha;
                if (i < VisualRotorCount && RotorDiscs[i])
                {
                    const float Direction = (i == 0 || i == 2) ? -1.0f : 1.0f;
                    RotorDiscs[i]->AddLocalRotation(
                        FRotator(0.0f, Direction * ActualRPM[i] * 6.0f * DeltaSeconds, 0.0f));
                }
            }
        }
        VelocityMps = FVector::ZeroVector;
        AngularVelocityRadS = FVector::ZeroVector;
        AccelMps2 = FVector::ZeroVector;

        FVector GroundLoc = CurrentLocation;
        GroundLoc.Z = 0.0f;
        SetActorLocation(GroundLoc);

        FRotator GroundRot = GetActorRotation();
        GroundRot.Roll = 0.0f;
        GroundRot.Pitch = 0.0f;
        AttitudeQuat = GroundRot.Quaternion();
    }
    else if (bHasSITL)
    {
        // --- AirSim-style motor-rotor model (ported from AirLib) ---
        // Control input (0-1) -> target RPM -> first-order RPM dynamics ->
        // quadratic thrust (T = k * RPM^2, propeller aerodynamics) + body drag.
        // Replaces the zero-delay linear thrust that caused ~0.45 Hz oscillation.
        float RollTorque = 0.f, PitchTorque = 0.f, YawTorque = 0.f;
        const float MotorAlpha = 1.0f - FMath::Exp(-DeltaSeconds / MotorTau);
        for (int32 i = 0; i < RotorCount; ++i)
        {
            // PX4 actuator output -> target RPM
            const float TargetRPM = FMath::Clamp(MotorThrusts[i], 0.f, 1.f) * MaxRPM;
            // RPM dynamics (first-order lag on rotational speed)
            ActualRPM[i] += (TargetRPM - ActualRPM[i]) * MotorAlpha;
            // Quadratic thrust: T = (RPM/maxRPM)^2 * MaxThrust
            const float RPMRatio = ActualRPM[i] / MaxRPM;
            const float T = RPMRatio * RPMRatio * MaxMotorThrustN;
            TotalThrustN += T;
            RollTorque  += T * MotorPY[i];   // 绕 X 轴（滚转）
            PitchTorque -= T * MotorPX[i];   // 绕 Y 轴（俯仰，前电机=低头）
            YawTorque   += T * MotorKM[i];   // 绕 Z 轴（偏航）
        }

        // Drive the visible propeller meshes from the same RPM state used by
        // the thrust model. Adjacent rotors spin in opposite directions, just
        // like the imported CW/CCW propeller assets.
        const int32 VisualRotorCount = FMath::Min(RotorCount, RotorDiscs.Num());
        for (int32 i = 0; i < VisualRotorCount; ++i)
        {
            if (UStaticMeshComponent* Rotor = RotorDiscs[i])
            {
                const float SpinDirection = (i == 0 || i == 2) ? -1.0f : 1.0f;
                const float SpinDegrees = SpinDirection * ActualRPM[i] * 6.0f * DeltaSeconds;
                Rotor->AddLocalRotation(FRotator(0.0f, SpinDegrees, 0.0f));
            }
        }

        // 推力从机体坐标系转到世界坐标系（沿机体 Z 轴向上）
        const FVector ThrustWorld = AttitudeQuat.RotateVector(FVector(0.f, 0.f, TotalThrustN));

        // 总加速度 = 推力/质量 + 风力 + 机体阻力 - 重力
        const FVector DragAccel = -VelocityMps * LinearDragCoeff;
        AccelMps2 = ThrustWorld / DroneMassKg + WindAccel + DragAccel - FVector(0.f, 0.f, 9.81f);

        // 角加速度（力矩/惯量 - 阻尼），简化惯量 ~0.012 kg·m²
        const float Inertia = 0.03f;
        const FVector AngAccel = FVector(RollTorque, PitchTorque, YawTorque) / Inertia
                                 - AngularVelocityRadS * AngularDamping;
        AngularVelocityRadS += AngAccel * DeltaSeconds;

        // 四元数积分更新姿态
        const float AngVelMag = AngularVelocityRadS.Size();
        if (AngVelMag > KINDA_SMALL_NUMBER)
        {
            const FQuat DeltaQ(AngularVelocityRadS / AngVelMag, AngVelMag * DeltaSeconds);
            // 机体角速度增量必须右乘（AttitudeQuat * DeltaQ）：先施加机体旋转再叠加到世界姿态。
            // 左乘(DeltaQ * AttitudeQuat)会把机体分量当成世界轴，姿态一倾斜即耦合发散→翻滚。
            AttitudeQuat = AttitudeQuat * DeltaQ;
            AttitudeQuat.Normalize();
        }
    }
    else
    {
        // --- 键盘兜底（无 SITL 连接时） ---
        FRotator Att = GetActorRotation();
        Att.Pitch = FMath::ClampAngle(Att.Pitch + PitchInput * AttitudeRateDegPerSec * DeltaSeconds, -80.f, 80.f);
        Att.Roll = FMath::ClampAngle(Att.Roll + RollInput * AttitudeRateDegPerSec * DeltaSeconds, -80.f, 80.f);
        Att.Yaw += YawInput * AttitudeRateDegPerSec * DeltaSeconds;
        AttitudeQuat = Att.Quaternion();

        const FVector CommandAccel = GetActorForwardVector() * (ThrottleInput * MaxCommandAccelMps2);
        AccelMps2 = CommandAccel + WindAccel;
    }

    SetActorRotation(AttitudeQuat);

    LastVelocityMps = VelocityMps;
    LastAccelMps2 = AccelMps2;
    VelocityMps += AccelMps2 * DeltaSeconds;
    VelocityMps = VelocityMps.GetClampedToMaxSize(45.0f);
    VelocityMps *= (1.f - LinearDamping * DeltaSeconds);

    const FVector MovementCm = VelocityMps * 100.0f * DeltaSeconds;
    if (bEnableHorizontalCollisionSweep)
    {
        FHitResult HorizontalHit;
        const FVector HorizontalDelta(MovementCm.X, MovementCm.Y, 0.0f);
        const FVector HorizontalTarget = GetActorLocation() + HorizontalDelta;
        SetActorLocation(HorizontalTarget, true, &HorizontalHit, ETeleportType::None);
        // A root mesh resting on the ground can report an initial-overlap hit
        // even for a horizontal sweep. Count only genuine side impacts with
        // meaningful horizontal motion; touchdown remains a separate event.
        const bool bHorizontalObstacleHit =
            HorizontalHit.bBlockingHit &&
            HorizontalDelta.SizeSquared() > 1.0f &&
            FMath::Abs(HorizontalHit.ImpactNormal.Z) < 0.60f;
        if (bHorizontalObstacleHit)
        {
            VelocityMps.X = 0.0f;
            VelocityMps.Y = 0.0f;
            if (CollisionLogCooldownSec <= 0.0f)
            {
                ++ObstacleCollisionCount;
                CollisionLogCooldownSec = 0.5f;
                UE_LOG(LogSZTUWindField, Warning,
                    TEXT("Urban collision #%d with %s at %s"),
                    ObstacleCollisionCount,
                    HorizontalHit.GetActor() ? *HorizontalHit.GetActor()->GetName() : TEXT("unknown"),
                    *HorizontalHit.ImpactPoint.ToString());
            }
        }
        SetActorLocation(GetActorLocation() + FVector(0.0f, 0.0f, MovementCm.Z));
    }
    else
    {
        SetActorLocation(GetActorLocation() + MovementCm);
    }
    CollisionLogCooldownSec = FMath::Max(0.0f, CollisionLogCooldownSec - DeltaSeconds);

    // 发送状态给 PX4 SITL
    if (SITLComponent && bHasSITL)
    {
        // 地面接触约束：防止穿透地面与长时间自由落体导致 IMU/高度异常
        FVector Loc = GetActorLocation();
        if (Loc.Z < 0.f)
        {
            Loc.Z = 0.f;
            SetActorLocation(Loc);
            if (VelocityMps.Z < 0.f) { VelocityMps.Z = 0.f; }
        }
        const bool bGrounded = (Loc.Z <= 0.5f) && (VelocityMps.Z <= 0.01f);

        // IMU 比力(真加速度) = 净加速度 + 重力（世界系 Z 上）。
        //   自由飞行: 比力 = 推力/质量 + 风（不含重力，重力在此抵消）
        //   着地/悬停: 比力 = +g（地面支持力），PX4 读到稳定的 +9.81
        // 坐标系：PX4 加速度计走 FRD 约定（X前 Y右 Z下）。静止/悬停时比力 f = -g：
        //   世界 NED 下 g=(0,0,+9.81)，零姿态 FRD=NED，故 FRD 比力 = (0,0,-9.81)。
        // UE 机体比力在 Z-up 下是 (0,0,+9.81)，转到 FRD 必须对 Z 取反 → 发 -9.81（正确）。
        // 此前曾误删该取反（发 +9.81），导致 PX4 姿态整体崩坏（attitude/heading 失败、mag_ratio=2）。
        // 陀螺仪 Z 的取反同理（机体 Z上 → FRD Z下），保留。
        FVector SpecForceWorld = AccelMps2 + FVector(0.f, 0.f, 9.81f);
        if (bGrounded) { SpecForceWorld = FVector(0.f, 0.f, 9.81f); }
        const FVector SpecForceBodyUE = AttitudeQuat.Inverse().RotateVector(SpecForceWorld);
        const FVector ImuAccel = FVector(SpecForceBodyUE.X, SpecForceBodyUE.Y, -SpecForceBodyUE.Z);
        // 角速度 ω 是【轴向量(pseudovector)】，在 FRU→FRD 的相似变换 C·R·C 下，其变换规则与
        // 极向量(加速度/速度)不同：
        //   极向量: v_frd = C·v_ue = (vx, vy, -vz)            (C = diag(1,1,-1), det(C) = -1)
        //   轴向量: ω_frd = det(C)·C·ω_ue = -C·ω_ue = (-ωx, -ωy, +ωz)
        // 因为 det(C) = -1，轴向量需额外乘一个全局负号。这恰好与四元数 C·R·C 严格一致：
        // PX4 收到 FRD 姿态 + 与之严格同向的 FRD 角速度，速率控制闭环符号才正确。
        // 若写成 (ωx,ωy,-ωz)（仅当极向量处理、漏掉 det(C)）则三轴全反 → 速率控制器收到反号速率
        // → 正反馈、双轴狂转、始终不离地（此前翻滚根因之一）。
        const FVector ImuGyro = FVector(-AngularVelocityRadS.X, -AngularVelocityRadS.Y, AngularVelocityRadS.Z);
        // 速度转 NED (Z 取反) 供 HIL_STATE / HIL_GPS 使用
        const FVector VelNED = FVector(VelocityMps.X, VelocityMps.Y, -VelocityMps.Z);

        SITLComponent->SendDroneState(
            GetActorLocation() / 100.0f,
            VelNED,
            GetActorQuat(),
            ImuGyro,
            ImuAccel);
    }

    UpdatePerception(DeltaSeconds);

    // Vision frame capture (periodic, low-rate for navigation)
    if (bVisionCaptureEnabled)
    {
        // Keep the capture camera on a stabilized gimbal: ignore body
        // roll/pitch, follow yaw only, look forward-down by VisionGimbalPitchDeg.
        UpdateVisionGimbal();

        VisionCaptureTimer += DeltaSeconds;
        if (VisionCaptureTimer >= VisionCaptureIntervalSec)
        {
            VisionCaptureTimer = 0.0f;
            CaptureVisionFrame();
        }
    }

    const FSZTUEnvelopeState EnvelopeState = EnvelopeMonitor ? EnvelopeMonitor->Evaluate(WindSample, GetActorRotation(), VelocityMps, LastAccelMps2, DeltaSeconds) : FSZTUEnvelopeState();
    SendTelemetry(WindSample, EnvelopeState);

    // 调试日志（每实例独立计数器，避免双机时只有一个实例的日志被打印）
    FrameCount++;
    if (FrameCount % 60 == 0)
    {
        if (bHasSITL)
        {
            UE_LOG(LogSZTUWindField, Display, TEXT("SITL[id=%d] %s: controls=%d thrust=%.1fN V=(%.1f,%.1f,%.1f) angVel=(%.2f,%.2f,%.2f)"),
                DroneId, bHexaX ? TEXT("Hexa-X") : TEXT("Quad-X"), RotorCount, TotalThrustN,
                VelocityMps.X, VelocityMps.Y, VelocityMps.Z,
                AngularVelocityRadS.X, AngularVelocityRadS.Y, AngularVelocityRadS.Z);
        }
        else
        {
            UE_LOG(LogSZTUWindField, Display, TEXT("Keyboard[id=%d]: Vwind=(%.1f,%.1f,%.1f) Vdrone=(%.1f,%.1f,%.1f) Accel=%.1f WindAccel=%.1f [SITL not connected - TCP %d]"),
                DroneId,
                WindSample.VelocityMps.X, WindSample.VelocityMps.Y, WindSample.VelocityMps.Z,
                VelocityMps.X, VelocityMps.Y, VelocityMps.Z,
                AccelMps2.Size(), WindAccel.Size(),
                SITLComponent ? SITLComponent->GetTransportPort() : 0);
        }
    }
}

void ASZTUDronePawn::SetThrottle(float Value) { ThrottleInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetPitch(float Value) { PitchInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetRoll(float Value) { RollInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetYaw(float Value) { YawInput = FMath::Clamp(Value, -1.0f, 1.0f); }

void ASZTUDronePawn::GetProximityRanges(float& FrontM, float& LeftM, float& RightM, float& DownM) const
{
    FrontM = FrontRangeM;
    LeftM = LeftRangeM;
    RightM = RightRangeM;
    DownM = DownRangeM;
}

void ASZTUDronePawn::UpdatePerception(float DeltaSeconds)
{
    if (!bEnableProximitySensors || !GetWorld()) { return; }

    PerceptionTimer += DeltaSeconds;
    const float Interval = 1.0f / FMath::Max(1.0f, ProximityUpdateRateHz);
    if (PerceptionTimer < Interval) { return; }
    PerceptionTimer = 0.0f;

    const float MaxRangeCm = FMath::Max(100.0f, ProximityMaxRangeM * 100.0f);
    const FVector SensorOrigin = GetActorLocation() + GetActorQuat().RotateVector(FVector(24.0f, 0.0f, 15.0f));
    FCollisionQueryParams QueryParams(SCENE_QUERY_STAT(SZTUProximitySensor), true, this);
    QueryParams.AddIgnoredActor(this);
    SensorHitMask = 0;

    auto TraceWorldDirection = [&](const FVector& WorldDirection, int32 SensorBit) -> float
    {
        const FVector Direction = WorldDirection.GetSafeNormal();
        const FVector End = SensorOrigin + Direction * MaxRangeCm;
        FHitResult Hit;
        const bool bHit = GetWorld()->LineTraceSingleByChannel(
            Hit, SensorOrigin, End, ECC_Visibility, QueryParams);
        if (bHit && Hit.bBlockingHit)
        {
            SensorHitMask |= (1 << SensorBit);
        }
        if (bShowSensorRays)
        {
            DrawDebugLine(GetWorld(), SensorOrigin, bHit ? Hit.ImpactPoint : End,
                bHit ? FColor::Red : FColor::Green, false, Interval * 1.5f, 0, 1.5f);
        }
        return bHit ? FMath::Clamp(Hit.Distance / 100.0f, 0.0f, ProximityMaxRangeM) : ProximityMaxRangeM;
    };

    auto TraceBodyYawFan = [&](const TArray<float>& YawAnglesDeg, int32 SensorBit) -> float
    {
        float RangeM = ProximityMaxRangeM;
        for (const float YawDeg : YawAnglesDeg)
        {
            const FVector LocalDirection = FRotator(0.0f, YawDeg, 0.0f).RotateVector(FVector::ForwardVector);
            RangeM = FMath::Min(RangeM,
                TraceWorldDirection(GetActorQuat().RotateVector(LocalDirection), SensorBit));
        }
        return RangeM;
    };

    FrontRangeM = TraceBodyYawFan({ -12.0f, 0.0f, 12.0f }, 0);
    LeftRangeM = TraceBodyYawFan({ -70.0f, -45.0f, -25.0f }, 1);
    RightRangeM = TraceBodyYawFan({ 25.0f, 45.0f, 70.0f }, 2);
    DownRangeM = TraceWorldDirection(FVector::DownVector, 3);

    CurrentSemanticZone = TEXT("transit");
    float NearestZoneDistanceM = TNumericLimits<float>::Max();
    const FVector CurrentLocation = GetActorLocation();
    for (TActorIterator<AActor> It(GetWorld()); It; ++It)
    {
        AActor* Candidate = *It;
        if (!Candidate || !Candidate->ActorHasTag(TEXT("SZTU.SemanticAnchor"))) { continue; }

        const FVector Delta = Candidate->GetActorLocation() - CurrentLocation;
        const float HorizontalDistanceM = FVector2D(Delta.X, Delta.Y).Size() / 100.0f;
        if (HorizontalDistanceM > 2.5f || HorizontalDistanceM >= NearestZoneDistanceM) { continue; }

        for (const FName& Tag : Candidate->Tags)
        {
            const FString TagText = Tag.ToString();
            if (TagText.StartsWith(TEXT("semantic:")))
            {
                CurrentSemanticZone = TagText.RightChop(9);
                NearestZoneDistanceM = HorizontalDistanceM;
                break;
            }
        }
    }
}

void ASZTUDronePawn::LocateOrSpawnWindField()
{
    if (!GetWorld() || bWindFieldLookupComplete) { return; }

    // 1) 优先使用编辑器中显式指派的引用（真正的“独立”组合方式）
    if (WindFieldReference)
    {
        WindField = WindFieldReference;
        bWindFieldLookupComplete = true;
        return;
    }

    // 2) 否则全局查找场景中已手动摆放的风场
    for (TActorIterator<ASZTUWindFieldActor> It(GetWorld()); It; ++It)
    {
        WindField = *It;
        bWindFieldLookupComplete = true;
        return;
    }

    // 3) 都没有，且允许自动生成时才生成一个（保持向后兼容）
    if (bAutoSpawnWindField)
    {
        FActorSpawnParameters Params;
        Params.Name = TEXT("SZTU_WindField_3200Fans");
        WindField = GetWorld()->SpawnActor<ASZTUWindFieldActor>(ASZTUWindFieldActor::StaticClass(), FVector::ZeroVector, FRotator::ZeroRotator, Params);
        bWindFieldLookupComplete = true;
        UE_LOG(LogSZTUWindField, Display, TEXT("Drone auto-spawned wind field."));
    }
    else
    {
        bWindFieldLookupComplete = true;
        UE_LOG(LogSZTUWindField, Display, TEXT("No wind field assigned/found and auto-spawn disabled; drone runs without wind sampling."));
    }
}

void ASZTUDronePawn::OpenTelemetrySocket()
{
    if (TelemetrySocket) { return; }
    FIPv4Address Address;
    if (!FIPv4Address::Parse(TelemetryHost, Address))
    {
        UE_LOG(LogSZTUWindField, Warning, TEXT("Invalid telemetry host: %s"), *TelemetryHost);
        return;
    }
    RemoteEndpoint = FIPv4Endpoint(Address, TelemetryPort);
    TelemetrySocket = FUdpSocketBuilder(TEXT("SZTU_PX4_Telemetry")).AsReusable().WithSendBufferSize(1024 * 1024);
    if (TelemetrySocket)
        UE_LOG(LogSZTUWindField, Display, TEXT("UDP telemetry enabled: %s:%d"), *TelemetryHost, TelemetryPort);

    // Local mirror endpoint (Windows loopback) for the web dashboard viewer.
    if (bLocalTelemetryMirror)
    {
        FIPv4Address Loopback;
        if (FIPv4Address::Parse(TEXT("127.0.0.1"), Loopback))
        {
            LocalTelemetryEndpoint = FIPv4Endpoint(Loopback, TelemetryPort);
            UE_LOG(LogSZTUWindField, Display, TEXT("UDP telemetry mirrored to local: 127.0.0.1:%d"), TelemetryPort);
        }
    }
}

void ASZTUDronePawn::CloseTelemetrySocket()
{
    if (!TelemetrySocket) { return; }
    TelemetrySocket->Close();
    ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(TelemetrySocket);
    TelemetrySocket = nullptr;
}

void ASZTUDronePawn::SendTelemetry(const FSZTUWindSample& WindSample, const FSZTUEnvelopeState& EnvelopeState) const
{
    if (!TelemetrySocket || !GetWorld()) { return; }
    TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
    const FVector PositionM = GetActorLocation() / 100.0f;
    const FRotator Attitude = GetActorRotation();
    Obj->SetNumberField(TEXT("time_s"), GetWorld()->GetTimeSeconds());
    Obj->SetNumberField(TEXT("position_x_m"), PositionM.X);
    Obj->SetNumberField(TEXT("position_y_m"), PositionM.Y);
    Obj->SetNumberField(TEXT("position_z_m"), PositionM.Z);
    Obj->SetNumberField(TEXT("velocity_x_mps"), VelocityMps.X);
    Obj->SetNumberField(TEXT("velocity_y_mps"), VelocityMps.Y);
    Obj->SetNumberField(TEXT("velocity_z_mps"), VelocityMps.Z);
    Obj->SetNumberField(TEXT("roll_deg"), Attitude.Roll);
    Obj->SetNumberField(TEXT("pitch_deg"), Attitude.Pitch);
    Obj->SetNumberField(TEXT("yaw_deg"), Attitude.Yaw);
    Obj->SetNumberField(TEXT("wind_x_mps"), WindSample.VelocityMps.X);
    Obj->SetNumberField(TEXT("wind_y_mps"), WindSample.VelocityMps.Y);
    Obj->SetNumberField(TEXT("wind_z_mps"), WindSample.VelocityMps.Z);
    Obj->SetNumberField(TEXT("wind_speed_mps"), WindSample.SpeedMps);
    Obj->SetNumberField(TEXT("gust_delta_mps"), WindSample.GustDeltaMps);
    Obj->SetBoolField(TEXT("proximity_enabled"), bEnableProximitySensors);
    Obj->SetNumberField(TEXT("range_front_m"), FrontRangeM);
    Obj->SetNumberField(TEXT("range_left_m"), LeftRangeM);
    Obj->SetNumberField(TEXT("range_right_m"), RightRangeM);
    Obj->SetNumberField(TEXT("range_down_m"), DownRangeM);
    Obj->SetNumberField(TEXT("sensor_hit_mask"), SensorHitMask);
    Obj->SetNumberField(TEXT("obstacle_collision_count"), ObstacleCollisionCount);
    Obj->SetStringField(TEXT("semantic_zone"), CurrentSemanticZone);
    Obj->SetBoolField(TEXT("onboard_camera_active"), OnboardCamera && OnboardCamera->IsActive());
    Obj->SetBoolField(TEXT("envelope_exceeded"), EnvelopeState.bExceeded);
    Obj->SetStringField(TEXT("envelope_reason"), EnvelopeState.Reason);
    FString Payload;
    TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Payload);
    if (!FJsonSerializer::Serialize(Obj.ToSharedRef(), Writer)) { return; }
    FTCHARToUTF8 Converted(*Payload);
    int32 BytesSent = 0;
    TelemetrySocket->SendTo(reinterpret_cast<const uint8*>(Converted.Get()), Converted.Length(), BytesSent, *RemoteEndpoint.ToInternetAddr());
    // Mirror to local loopback for the Windows-side dashboard viewer.
    if (bLocalTelemetryMirror)
    {
        int32 LocalBytesSent = 0;
        TelemetrySocket->SendTo(reinterpret_cast<const uint8*>(Converted.Get()), Converted.Length(), LocalBytesSent, *LocalTelemetryEndpoint.ToInternetAddr());
    }
}

// --- Vision frame capture ---

void ASZTUDronePawn::SetupVisionCapture()
{
    bVisionCaptureEnabled = FParse::Param(FCommandLine::Get(), TEXT("VisionCapture"));
    bArchiveVisionFrames = FParse::Param(FCommandLine::Get(), TEXT("ArchiveVisionFrames"));

    // Also auto-enable in urban semantic scene
    if (!bVisionCaptureEnabled)
    {
        FString MapName = GetWorld() ? GetWorld()->GetName() : TEXT("");
        if (MapName.Contains(TEXT("UrbanSemantic")) || MapName.Contains(TEXT("Semantic")))
        {
            bVisionCaptureEnabled = true;
        }
    }

    // Optional positive depression-angle override: -VisionGimbalPitch=35
    float GimbalOverride = 0.0f;
    if (FParse::Value(FCommandLine::Get(), TEXT("VisionGimbalPitch="), GimbalOverride) &&
        GimbalOverride >= 5.0f && GimbalOverride <= 60.0f)
    {
        VisionGimbalPitchDeg = GimbalOverride;
    }

    // Keep the default conservative, but allow the dashboard/regression
    // wrapper to request a modestly fresher preview. The upper bound protects
    // the HIL loop from excessive synchronous GPU readback.
    float CaptureHzOverride = 0.0f;
    if (FParse::Value(FCommandLine::Get(), TEXT("VisionCaptureHz="), CaptureHzOverride))
    {
        CaptureHzOverride = FMath::Clamp(CaptureHzOverride, 0.5f, 4.0f);
        VisionCaptureIntervalSec = 1.0f / CaptureHzOverride;
    }
    int32 CaptureWidthOverride = VisionCaptureWidth;
    int32 CaptureHeightOverride = VisionCaptureHeight;
    if (FParse::Value(FCommandLine::Get(), TEXT("VisionCaptureWidth="), CaptureWidthOverride))
    {
        VisionCaptureWidth = FMath::Clamp(CaptureWidthOverride, 320, 1920);
    }
    if (FParse::Value(FCommandLine::Get(), TEXT("VisionCaptureHeight="), CaptureHeightOverride))
    {
        VisionCaptureHeight = FMath::Clamp(CaptureHeightOverride, 180, 1080);
    }

    if (!bVisionCaptureEnabled || !VisionCapture) { return; }

    // The generated urban map owns exact-position, hollow ground logos with
    // distinct semantic colors. Do not spawn the old giant solid cubes here.

    // Create render target
    CaptureRenderTarget = NewObject<UTextureRenderTarget2D>(this);
    CaptureRenderTarget->RenderTargetFormat = ETextureRenderTargetFormat::RTF_RGBA8;
    CaptureRenderTarget->InitAutoFormat(VisionCaptureWidth, VisionCaptureHeight);
    VisionCapture->TextureTarget = CaptureRenderTarget;

    // Set output directory — one subdirectory per run (timestamp run id) so a
    // new mission never reads stale frames from a previous run.
    VisionCaptureBaseDir = FPaths::ProjectSavedDir() / TEXT("CameraFrames");
    VisionRunId = FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S"));
    VisionRunDir = VisionCaptureBaseDir / VisionRunId;
    IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
    if (!PlatformFile.DirectoryExists(*VisionRunDir))
    {
        PlatformFile.CreateDirectoryTree(*VisionRunDir);
    }
    FFileHelper::SaveStringToFile(
        VisionRunId,
        *(VisionCaptureBaseDir / TEXT("current_run.txt")),
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

    UE_LOG(LogSZTUWindField, Display,
        TEXT("Vision capture enabled: %dx%d @ %.1fHz -> %s (gimbal pitch %.1f deg, archive=%s)"),
        VisionCaptureWidth, VisionCaptureHeight,
        1.0f / VisionCaptureIntervalSec,
        *VisionRunDir, VisionGimbalPitchDeg,
        bArchiveVisionFrames ? TEXT("true") : TEXT("false"));

    VisionCaptureTimer = 0.0f;
    VisionCaptureFrameCount = 0;
}

void ASZTUDronePawn::RefreshSemanticRoutePreview(float DeltaSeconds, bool bForce)
{
    SemanticRoutePreviewPollSeconds += DeltaSeconds;
    if (!bForce && SemanticRoutePreviewPollSeconds < 0.5f) { return; }
    SemanticRoutePreviewPollSeconds = 0.0f;
    if (SemanticRoutePreviewPath.IsEmpty() || !FPaths::FileExists(SemanticRoutePreviewPath)) { return; }

    const FDateTime Timestamp = IFileManager::Get().GetTimeStamp(*SemanticRoutePreviewPath);
    if (!bForce && Timestamp == SemanticRoutePreviewTimestamp) { return; }

    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *SemanticRoutePreviewPath)) { return; }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid()) { return; }

    const TArray<TSharedPtr<FJsonValue>>* RoutePoints = nullptr;
    if (!Root->TryGetArrayField(TEXT("route_points"), RoutePoints) || RoutePoints->Num() < 2) { return; }

    TArray<FVector> ParsedPoints;
    TArray<FString> ParsedLabels;
    for (const TSharedPtr<FJsonValue>& Value : *RoutePoints)
    {
        const TSharedPtr<FJsonObject> Point = Value.IsValid() ? Value->AsObject() : nullptr;
        if (!Point.IsValid()) { continue; }
        double X = 0.0, Y = 0.0, Z = 0.0;
        if (!Point->TryGetNumberField(TEXT("x_m"), X)
            || !Point->TryGetNumberField(TEXT("y_m"), Y)
            || !Point->TryGetNumberField(TEXT("z_m"), Z)) { continue; }
        ParsedPoints.Add(SemanticRouteOriginWorld + FVector(X, Y, Z) * 100.0f);
        FString Label;
        Point->TryGetStringField(TEXT("label"), Label);
        ParsedLabels.Add(Label);
    }
    if (ParsedPoints.Num() < 2) { return; }
    SemanticRoutePointsWorld = MoveTemp(ParsedPoints);
    SemanticRoutePointLabels = MoveTemp(ParsedLabels);
    SemanticRoutePreviewTimestamp = Timestamp;
    UE_LOG(LogSZTUWindField, Display,
        TEXT("Semantic UE5 trajectory loaded: %d points from %s"),
        SemanticRoutePointsWorld.Num(), *SemanticRoutePreviewPath);
}

void ASZTUDronePawn::DrawSemanticRoutePreview() const
{
    UWorld* World = GetWorld();
    if (!World || SemanticRoutePointsWorld.Num() < 2) { return; }

    const FColor RouteColor(80, 230, 255);
    const FColor ArrowColor(180, 80, 255);
    const FColor WaypointColor(255, 210, 45);
    for (int32 Index = 1; Index < SemanticRoutePointsWorld.Num(); ++Index)
    {
        const FVector& Start = SemanticRoutePointsWorld[Index - 1];
        const FVector& End = SemanticRoutePointsWorld[Index];
        DrawDebugLine(World, Start, End, RouteColor, false, -1.0f, 0, 3.0f);
        const FVector Direction = (End - Start).GetSafeNormal();
        const FVector Mid = FMath::Lerp(Start, End, 0.55f);
        DrawDebugDirectionalArrow(World, Mid - Direction * 35.0f, Mid + Direction * 35.0f,
            18.0f, ArrowColor, false, -1.0f, 0, 3.0f);
        DrawDebugSphere(World, End, 12.0f, 12, WaypointColor, false, -1.0f, 0, 2.0f);
    }
    DrawDebugSphere(World, SemanticRoutePointsWorld[0], 14.0f, 12,
        FColor::Green, false, -1.0f, 0, 2.0f);
}

void ASZTUDronePawn::SpawnSemanticLandmarks()
{
    UWorld* World = GetWorld();
    if (!World) { return; }

    // Goal color identity (must match the Python vision_detector color map).
    // start_pad is skipped: the drone starts there, so a marker would overlap
    // the spawn point and pollute proximity/collision telemetry.
    struct FGoalMarker { FString Id; FVector2D Pos; FLinearColor Color; };
    TArray<FGoalMarker> Goals = {
        { TEXT("inspection_gate"), FVector2D(6.0f, -4.0f),  FLinearColor::Blue },
        { TEXT("delivery_zone"),   FVector2D(18.0f, 0.0f),  FLinearColor::Green },
        { TEXT("medical_station"), FVector2D(18.0f, 4.0f),  FLinearColor::Red },
    };

    // Allow the JSON map to override coordinates (mirrors urban_semantic_map.json).
    FString JsonPath = FPaths::ProjectDir() / TEXT("urban_semantic_map.json");
    if (FPaths::FileExists(JsonPath))
    {
        FString JsonText;
        if (FFileHelper::LoadFileToString(JsonText, *JsonPath))
        {
            TSharedPtr<FJsonObject> Root;
            TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
            if (FJsonSerializer::Deserialize(Reader, Root) && Root.IsValid())
            {
                const TSharedPtr<FJsonObject>* GoalsObj = nullptr;
                if (Root->TryGetObjectField(TEXT("goals"), GoalsObj))
                {
                    TArray<FGoalMarker> Parsed;
                    for (const auto& Pair : (*GoalsObj)->Values)
                    {
                        const TSharedPtr<FJsonObject>& Goal = Pair.Value->AsObject();
                        if (!Goal.IsValid()) { continue; }
                        const TArray<TSharedPtr<FJsonValue>>* PosArr = nullptr;
                        if (!Goal->TryGetArrayField(TEXT("position_m"), PosArr) || PosArr->Num() < 2) { continue; }
                        FGoalMarker M;
                        M.Id = Pair.Key;
                        M.Pos = FVector2D(static_cast<float>((*PosArr)[0]->AsNumber()),
                                          static_cast<float>((*PosArr)[1]->AsNumber()));
                        // Keep the hardcoded color identity; only override position.
                        for (const FGoalMarker& G : Goals)
                        {
                            if (G.Id == M.Id) { M.Color = G.Color; break; }
                        }
                        Parsed.Add(M);
                    }
                    if (Parsed.Num() > 0) { Goals = Parsed; }
                }
            }
        }
    }

    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    UMaterialInterface* BaseMat = LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    UE_LOG(LogSZTUWindField, Warning, TEXT("Landmark materials: Cube=%p BaseMat=%p"), Cube, BaseMat);

    // Create a STATIC holder actor that does NOT move with the drone.
    // Marker components attached to this holder stay at fixed world positions.
    FVector DroneLoc = GetActorLocation();
    AActor* LandmarkHolder = World->SpawnActor<AActor>(AActor::StaticClass(), DroneLoc, FRotator::ZeroRotator);
    if (!LandmarkHolder) { return; }
    USceneComponent* HolderRoot = NewObject<USceneComponent>(LandmarkHolder, TEXT("LandmarkHolderRoot"));
    HolderRoot->RegisterComponent();
    LandmarkHolder->SetRootComponent(HolderRoot);

    for (const FGoalMarker& Goal : Goals)
    {
        // Place the marker ~1.5m ahead of the goal, resting on the ground.
        // Goal.Pos is in meters; Unreal Units are cm.
        constexpr float CM_PER_M = 100.0f;
        FVector WorldPos = DroneLoc + FVector(
            (Goal.Pos.X + 1.5f) * CM_PER_M,
            Goal.Pos.Y * CM_PER_M,
            1.5f * CM_PER_M);

        // Create marker as a component attached to the STATIC holder (not the drone).
        UStaticMeshComponent* MarkerComp = NewObject<UStaticMeshComponent>(LandmarkHolder);
        if (!MarkerComp) { continue; }
        MarkerComp->RegisterComponent();
        if (Cube) { MarkerComp->SetStaticMesh(Cube); }
        MarkerComp->SetWorldScale3D(FVector(3.0f, 3.0f, 5.0f)); // ~6x6x10m — unmissable
        MarkerComp->SetWorldLocation(WorldPos);
        if (BaseMat)
        {
            UMaterialInstanceDynamic* MID = UMaterialInstanceDynamic::Create(BaseMat, this);
            if (MID)
            {
                // Set both base color AND emissive (BasicShapeMaterial may have either).
                FLinearColor BrightColor = Goal.Color * 10.0f; // boost for visibility
                MID->SetVectorParameterValue(TEXT("Color"), Goal.Color);
                MID->SetVectorParameterValue(TEXT("EmissiveColor"), BrightColor);
                MarkerComp->SetMaterial(0, MID);
            }
        }
        MarkerComp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        MarkerComp->SetCastShadow(false);
        MarkerComp->SetVisibility(true);
        MarkerComp->AttachToComponent(HolderRoot, FAttachmentTransformRules::KeepWorldTransform);

        // Add a bright colored point light at the marker so it glows regardless
        // of scene lighting or material emissive support.
        UPointLightComponent* MarkerLight = NewObject<UPointLightComponent>(LandmarkHolder);
        if (MarkerLight)
        {
            MarkerLight->RegisterComponent();
            MarkerLight->SetWorldLocation(WorldPos);
            MarkerLight->SetIntensity(80000.0f); // very bright
            MarkerLight->SetLightColor(FColor(
                FMath::Clamp(int32(Goal.Color.R * 255), 0, 255),
                FMath::Clamp(int32(Goal.Color.G * 255), 0, 255),
                FMath::Clamp(int32(Goal.Color.B * 255), 0, 255)));
            MarkerLight->SetAttenuationRadius(1500.0f); // 15m
            MarkerLight->SetCastShadows(false);
            MarkerLight->AttachToComponent(HolderRoot, FAttachmentTransformRules::KeepWorldTransform);
        }

        LandmarkMarkers.Add(MarkerComp);

        UE_LOG(LogSZTUWindField, Display,
            TEXT("Spawned semantic landmark '%s' (color=%.0f,%.0f,%.0f) at world (%s) light=%p"),
            *Goal.Id, Goal.Color.R, Goal.Color.G, Goal.Color.B, *WorldPos.ToString(), MarkerLight);
    }
}

void ASZTUDronePawn::UpdateVisionGimbal()
{
    // Stabilized gimbal: drop body roll/pitch, keep yaw, apply fixed forward-down
    // tilt. Applied to both the vision-capture camera and the onboard view camera
    // so the picture stays level regardless of airframe attitude.
    const FString MapName = GetWorld() ? GetWorld()->GetMapName() : FString();
    FRotator GimbalRot;
    if (MapName.Contains(TEXT("SZTULargeBridgeSemanticScene")) &&
        FParse::Param(FCommandLine::Get(), TEXT("DualCameraView")) && VisionCapture)
    {
        // During the bridge orbit, keep the inspection camera aimed at the
        // structure instead of following vehicle yaw into the empty sky.
        const FVector BridgeAimPoint(2750.0f, 0.0f, 100.0f);
        GimbalRot = (BridgeAimPoint - VisionCapture->GetComponentLocation()).Rotation();
    }
    else
    {
        const FRotator ActorRot = GetActorRotation();
        // Unreal uses negative camera pitch for a downward look direction.
        GimbalRot = FRotator(-VisionGimbalPitchDeg, ActorRot.Yaw, 0.0f);
    }
    if (VisionCapture)
    {
        const float DeltaSeconds = GetWorld() ? GetWorld()->GetDeltaSeconds() : (1.0f / 60.0f);
        VisionCapture->SetWorldRotation(FMath::RInterpTo(
            VisionCapture->GetComponentRotation(), GimbalRot, DeltaSeconds, 5.0f));
    }
    if (OnboardCamera)
    {
        const float DeltaSeconds = GetWorld() ? GetWorld()->GetDeltaSeconds() : (1.0f / 60.0f);
        OnboardCamera->SetWorldRotation(FMath::RInterpTo(
            OnboardCamera->GetComponentRotation(), GimbalRot, DeltaSeconds, 5.0f));
    }
}

void ASZTUDronePawn::CaptureVisionFrame()
{
    if (!bVisionCaptureEnabled || !VisionCapture || !CaptureRenderTarget) { return; }
    if (!IsInGameThread()) { return; }

    // Render only on demand. Capturing the scene every UE frame and then doing
    // a separate synchronous readback caused intermittent multi-second HIL
    // stalls. The low-rate semantic detector only needs one fresh frame/second.
    VisionCapture->CaptureScene();

    FTextureRenderTargetResource* RTResource = CaptureRenderTarget->GameThread_GetRenderTargetResource();
    if (!RTResource) { return; }

    TArray<FColor> Pixels;
    RTResource->ReadPixels(Pixels);

    if (Pixels.Num() == 0) { return; }

    // Keep a fixed eight-frame ring. WSL directory enumeration over /mnt/d
    // becomes extremely slow once a run accumulates thousands of PNGs, and the
    // vision controller only needs a short temporal voting window.
    constexpr int32 VisionFrameRingSize = 8;
    const int32 RingSlot = VisionCaptureFrameCount++ % VisionFrameRingSize;
    FString FileName = FString::Printf(TEXT("latest_%02d.png"), RingSlot);
    FString FullPath = VisionRunDir / FileName;

    IImageWrapperModule& ImageWrapperModule = FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
    TSharedPtr<IImageWrapper> ImageWrapper = ImageWrapperModule.CreateImageWrapper(EImageFormat::PNG);
    if (ImageWrapper.IsValid())
    {
        TArray<uint8> RawData;
        RawData.SetNumUninitialized(Pixels.Num() * 4);
        for (int32 i = 0; i < Pixels.Num(); ++i)
        {
            const FColor& Color = Pixels[i];
            RawData[i * 4 + 0] = Color.R;
            RawData[i * 4 + 1] = Color.G;
            RawData[i * 4 + 2] = Color.B;
            RawData[i * 4 + 3] = Color.A;
        }
        ImageWrapper->SetRaw(RawData.GetData(), RawData.Num(), VisionCaptureWidth, VisionCaptureHeight, ERGBFormat::RGBA, 8);

        TArray64<uint8> PngData = ImageWrapper->GetCompressed(100);
        if (PngData.Num() > 0)
        {
            FFileHelper::SaveArrayToFile(PngData, *FullPath);
            if (bArchiveVisionFrames)
            {
                const FString ArchiveName = FString::Printf(
                    TEXT("frame_%06d.png"), VisionCaptureFrameCount);
                FFileHelper::SaveArrayToFile(PngData, *(VisionRunDir / ArchiveName));
            }
        }
    }
}
