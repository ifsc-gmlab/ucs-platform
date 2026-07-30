#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SZTUWindFieldActor.h"
#include "SZTUEnvelopeMonitorComponent.h"
#include "SZTUDronePawn.generated.h"

class FSocket;
class UCameraComponent;
class USceneCaptureComponent2D;
class UTextureRenderTarget2D;
class UStaticMeshComponent;
class AStaticMeshActor;
class USZTUMavlinkSITLComponent;

UENUM(BlueprintType)
enum class ESZTUMultirotorAirframe : uint8
{
    QuadX,
    HexaX
};

UCLASS(Blueprintable)
class SZTUWINDFIELDPX4DEMO_API ASZTUDronePawn : public APawn
{
    GENERATED_BODY()

public:
    ASZTUDronePawn();

    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
    virtual void OnConstruction(const FTransform& Transform) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UStaticMeshComponent* Body;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UStaticMeshComponent* IrisBody;

    // Forward-facing onboard camera. Use -OnboardCamera to make it the active
    // player view; the default observer camera remains unchanged otherwise.
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UCameraComponent* OnboardCamera;

    // Vision capture camera for semantic detection. Shares the same transform as
    // OnboardCamera but renders to a UTextureRenderTarget2D and periodically
    // saves PNG frames to Saved/CameraFrames/ for the Python vision pipeline.
    // Activated by -VisionCapture command-line flag (or always in urban scenes).
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USceneCaptureComponent2D* VisionCapture;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    TArray<UStaticMeshComponent*> RotorDiscs;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USZTUEnvelopeMonitorComponent* EnvelopeMonitor;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USZTUMavlinkSITLComponent* SITLComponent = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    ESZTUMultirotorAirframe Airframe = ESZTUMultirotorAirframe::QuadX;

    // 双机并行时的无人机 ID。DroneId=1 → TCP 4560，DroneId=2 → TCP 4561。
    // BeginPlay 时按 DroneId 设 SITLComponent->ListenPort = 4559 + DroneId。
    // 单机场景默认 DroneId=1 跟旧行为一致（4560 端口）。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics", meta = (ClampMin = "1", ClampMax = "4"))
    int32 DroneId = 1;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flight")
    float MaxCommandAccelMps2 = 15.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flight")
    float AttitudeRateDegPerSec = 85.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flight")
    float WindDragGain = 0.35f;

    // 旋翼相对机身的水平偏移（X/Y 独立，适配 iris 非对称机臂）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visual")
    float RotorOffsetX = 14.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visual")
    float RotorOffsetY = 23.0f;

    // 旋翼额外缩放（在导入缩放基础上再乘）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visual")
    float RotorScaleMultiplier = 1.0f;

    // 机身颜色（运行时创建固定颜色材质，无需材质资产有参数）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visual", meta = (DisplayName = "Body Color"))
    FLinearColor BodyColor = FLinearColor(1.0f, 0.42f, 0.0f, 1.0f); // 鲜艳橙色

    // 桨叶颜色
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visual", meta = (DisplayName = "Propeller Color"))
    FLinearColor PropellerColor = FLinearColor(0.04f, 0.04f, 0.05f, 1.0f); // 近黑深灰

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Telemetry")
    FString TelemetryHost = TEXT("127.0.0.1");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Telemetry")
    int32 TelemetryPort = 14660;

    // Multi-direction proximity sensing used by the urban obstacle-avoidance
    // controller. Each horizontal sensor is a three-ray fan; the downward
    // sensor behaves as a simple range finder.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Perception")
    bool bEnableProximitySensors = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Perception", meta = (ClampMin = "1.0", ClampMax = "50.0"))
    float ProximityMaxRangeM = 12.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Perception", meta = (ClampMin = "1.0", ClampMax = "60.0"))
    float ProximityUpdateRateHz = 10.0f;

    // Existing flight scenes use transform integration without collision
    // sweeps. The urban scene enables horizontal sweeps so failed avoidance
    // cannot visually pass through a building while leaving touchdown logic
    // unchanged.
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Perception")
    bool bEnableHorizontalCollisionSweep = false;

    UFUNCTION(BlueprintCallable, Category = "Perception")
    void GetProximityRanges(float& FrontM, float& LeftM, float& RightM, float& DownM) const;

    UFUNCTION(BlueprintCallable, Category = "Perception")
    FString GetCurrentSemanticZone() const { return CurrentSemanticZone; }

    // 风场引用：编辑器中直接指派（真正的“独立”），留空则运行时全局查找，最后才按开关自动生成。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Field")
    ASZTUWindFieldActor* WindFieldReference = nullptr;

    // 当场景里既没指派引用、也没手动摆放风场时，是否允许本 Pawn 自动生成一个。
    // 默认开启以保持旧行为；在"纯无人机"测试场景里把它关掉即可得到干净、独立的环境。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Field")
    bool bAutoSpawnWindField = true;

    // --- PX4 SITL 物理参数 ---
    // 无人机质量（kg，iris 约 0.8kg）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    float DroneMassKg = 0.85f;

    // 单电机最大推力（N，iris 每电机约 4N，4电机共16N > 悬停需8.3N）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    float MaxMotorThrustN = 4.5f;

    // 角阻尼系数（模拟空气阻力，防止角速度无限增大）
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    float AngularDamping = 2.0f;

    // 线性阻尼系数
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    float LinearDamping = 0.2f;

private:
    void ApplyMaterialsAtRuntime();
    void SetThrottle(float Value);
    void SetPitch(float Value);
    void SetRoll(float Value);
    void SetYaw(float Value);
    void LocateOrSpawnWindField();
    void OpenTelemetrySocket();
    void CloseTelemetrySocket();
    void SendTelemetry(const FSZTUWindSample& WindSample, const FSZTUEnvelopeState& EnvelopeState) const;
    void UpdatePerception(float DeltaSeconds);

    float ThrottleInput = 0.0f;
    float PitchInput = 0.0f;
    float RollInput = 0.0f;
    float YawInput = 0.0f;

    // 调试日志帧计数器（每实例独立，避免 static 导致双机只有一个实例打印日志）
    int32 FrameCount = 0;

    FVector VelocityMps = FVector::ZeroVector;
    FVector LastVelocityMps = FVector::ZeroVector;
    FVector LastAccelMps2 = FVector::ZeroVector;
    FVector AngularVelocityRadS = FVector::ZeroVector;  // 机体角速度（rad/s），用于 SITL 传感器

    // AirSim-style motor-rotor model (ported from AirLib Px4MultiRotor).
    // PX4 actuator output (0-1) drives target RPM; actual RPM lags via
    // first-order dynamics; thrust is quadratic in RPM (propeller aero).
    // This replaces the original zero-delay linear thrust that caused
    // the ~0.45 Hz limit-cycle oscillation.
    float ActualRPM[8] = {};
    static constexpr float MaxRPM = 9500.0f;        // AirSim F450 rotor_params.max_rpm
    static constexpr float MotorTau = 0.02f;         // 20ms ESC+motor response (reduced from 50ms for EKF stability)
    static constexpr float LinearDragCoeff = 4.0f;   // body linear drag (AirSim F450: default * 4)

    UPROPERTY()
    ASZTUWindFieldActor* WindField = nullptr;
    bool bWindFieldLookupComplete = false;

    FSocket* TelemetrySocket = nullptr;
    FIPv4Endpoint RemoteEndpoint;
    // Always mirror telemetry to local loopback so a Windows-side viewer
    // (e.g. the web dashboard) can read live data without touching the
    // critical WSL controller link.
    FIPv4Endpoint LocalTelemetryEndpoint;
    bool bLocalTelemetryMirror = true;

    float PerceptionTimer = 0.0f;
    float FrontRangeM = 12.0f;
    float LeftRangeM = 12.0f;
    float RightRangeM = 12.0f;
    float DownRangeM = 12.0f;
    int32 SensorHitMask = 0;
    int32 ObstacleCollisionCount = 0;
    float CollisionLogCooldownSec = 0.0f;
    bool bShowSensorRays = false;
    FString CurrentSemanticZone = TEXT("transit");

    // --- Vision frame capture ---
    UPROPERTY()
    UTextureRenderTarget2D* CaptureRenderTarget = nullptr;

    float VisionCaptureTimer = 0.0f;
    float VisionCaptureIntervalSec = 1.0f;   // 1 Hz; temporal voting does not need video rate
    int32 VisionCaptureFrameCount = 0;
    bool bVisionCaptureEnabled = false;
    int32 VisionCaptureWidth = 320;           // Keep synchronous GPU readback lightweight
    int32 VisionCaptureHeight = 180;

    // Stabilized-gimbal forward-down tilt (degrees). The vision capture camera
    // ignores body roll/pitch and only follows yaw, then looks down by this
    // angle. Prevents the camera from pointing at the sky / black ground when
    // the airframe pitches during flight.
    UPROPERTY(EditAnywhere, Category = "Vision", meta = (ClampMin = "5.0", ClampMax = "60.0"))
    float VisionGimbalPitchDeg = 30.0f;

    // Per-run capture directory so a new mission never reads stale frames from
    // a previous run. VisionRunId is a timestamp; frames go into <BaseDir>/<RunId>/.
    FString VisionCaptureBaseDir;
    FString VisionRunDir;
    FString VisionRunId;

    void SetupVisionCapture();
    void CaptureVisionFrame();
    void UpdateVisionGimbal();

    // Spawns colored landmark markers (one per semantic goal) near the drone so
    // the onboard VisionCapture camera can visually identify which goal it is
    // approaching. Only runs in the urban semantic scene. Markers are visual
    // only (NoCollision) and are not saved to the map.
    void SpawnSemanticLandmarks();

    // Marker components attached to this pawn (guaranteed to render in SceneCapture).
    TArray<UStaticMeshComponent*> LandmarkMarkers;
};
