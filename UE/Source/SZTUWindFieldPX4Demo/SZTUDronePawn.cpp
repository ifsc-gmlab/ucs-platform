#include "SZTUDronePawn.h"
#include "Components/StaticMeshComponent.h"
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

ASZTUDronePawn::ASZTUDronePawn()
{
    PrimaryActorTick.bCanEverTick = true;

    // Iris 机身（已导入的模型）
    Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
    RootComponent = Body;
    Body->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    Body->SetSimulatePhysics(false);

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
    OpenTelemetrySocket();

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

    // Color by airframe: quad=orange, hex=blue.
    // REPLACE all body materials with M_DroneBodyColor (which we know has
    // a BaseColor parameter from the class default). The original mesh's
    // materials have their color baked into textures and don't expose
    // color parameters we can set at runtime.
    const FLinearColor VehicleColor = bHexaX
        ? FLinearColor(0.12f, 0.45f, 0.85f, 1.0f)  // blue #1F73CC
        : FLinearColor(0.85f, 0.45f, 0.10f, 1.0f);  // orange #D9731A
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
    }
    ApplyMaterialsAtRuntime();

    // 生成在风场前方方便观察。关键：Z 必须≈0（贴地），不能在空中！
    // 原因：PX4 连接(SITL 物理接管)后无人机才受重力；若生成在 1.2m 高处，
    // 连接瞬间会自由落体 ~0.5s，落体期间 IMU 比力=(0,0,0)、落地后=(0,0,9.81)，
    // EKF2 加速度计偏置估计器会把“本该是重力、却读成 0”误判成巨大偏置(~-9.81)，
    // 永久触发 "High Accelerometer Bias" + "vertical velocity unstable"，且重力/偏置一旦锁定无法自恢复。
    // 贴地生成则连接瞬间即 bGrounded=true、IMU 恒为 (0,0,9.81)，偏置直接收敛到 0。
    SetActorLocation(FVector(250.0f, 0.0f, 0.0f));
    UE_LOG(LogSZTUWindField, Display, TEXT("Drone spawned at %s"), *GetActorLocation().ToString());
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
            CommandedThrustN += FMath::Clamp(MotorThrusts[i], 0.f, 1.f) * MaxMotorThrustN;
        }
    }

    const bool bManualInputActive =
        FMath::Abs(ThrottleInput) > 0.01f ||
        FMath::Abs(PitchInput) > 0.01f ||
        FMath::Abs(RollInput) > 0.01f ||
        FMath::Abs(YawInput) > 0.01f;
    const FVector CurrentLocation = GetActorLocation();
    const bool bNearGround = CurrentLocation.Z <= 0.5f;
    const bool bLiftCommanded = CommandedThrustN >= DroneMassKg * 9.81f * 0.95f;
    const bool bAirborne = CurrentLocation.Z > 5.0f;
    const bool bGroundLocked =
        bNearGround &&
        ((!bHasSITL && !bManualInputActive) || (bHasSITL && !bLiftCommanded && !bAirborne));

    const FVector WindAccel = bGroundLocked ? FVector::ZeroVector : (WindSample.VelocityMps - VelocityMps) * WindDragGain;

    if (bGroundLocked)
    {
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

    SetActorLocation(GetActorLocation() + VelocityMps * 100.0f * DeltaSeconds);

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

    const FSZTUEnvelopeState EnvelopeState = EnvelopeMonitor ? EnvelopeMonitor->Evaluate(WindSample, GetActorRotation(), VelocityMps, LastAccelMps2, DeltaSeconds) : FSZTUEnvelopeState();
    SendTelemetry(WindSample, EnvelopeState);

    // 调试日志
    static int32 FrameCount = 0;
    if (++FrameCount % 60 == 0)
    {
        if (bHasSITL)
        {
            UE_LOG(LogSZTUWindField, Display, TEXT("SITL %s: controls=%d thrust=%.1fN V=(%.1f,%.1f,%.1f) angVel=(%.2f,%.2f,%.2f)"),
                bHexaX ? TEXT("Hexa-X") : TEXT("Quad-X"), RotorCount, TotalThrustN,
                VelocityMps.X, VelocityMps.Y, VelocityMps.Z,
                AngularVelocityRadS.X, AngularVelocityRadS.Y, AngularVelocityRadS.Z);
        }
        else
        {
            UE_LOG(LogSZTUWindField, Display, TEXT("Keyboard: Vwind=(%.1f,%.1f,%.1f) Vdrone=(%.1f,%.1f,%.1f) Accel=%.1f WindAccel=%.1f [SITL not connected - TCP 4560]"),
                WindSample.VelocityMps.X, WindSample.VelocityMps.Y, WindSample.VelocityMps.Z,
                VelocityMps.X, VelocityMps.Y, VelocityMps.Z,
                AccelMps2.Size(), WindAccel.Size());
        }
    }
}

void ASZTUDronePawn::SetThrottle(float Value) { ThrottleInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetPitch(float Value) { PitchInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetRoll(float Value) { RollInput = FMath::Clamp(Value, -1.0f, 1.0f); }
void ASZTUDronePawn::SetYaw(float Value) { YawInput = FMath::Clamp(Value, -1.0f, 1.0f); }

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
    Obj->SetBoolField(TEXT("envelope_exceeded"), EnvelopeState.bExceeded);
    Obj->SetStringField(TEXT("envelope_reason"), EnvelopeState.Reason);
    FString Payload;
    TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Payload);
    if (!FJsonSerializer::Serialize(Obj.ToSharedRef(), Writer)) { return; }
    FTCHARToUTF8 Converted(*Payload);
    int32 BytesSent = 0;
    TelemetrySocket->SendTo(reinterpret_cast<const uint8*>(Converted.Get()), Converted.Length(), BytesSent, *RemoteEndpoint.ToInternetAddr());
}
