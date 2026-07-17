#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SZTUWindFieldActor.h"
#include "SZTUEnvelopeMonitorComponent.h"
#include "SZTUDronePawn.generated.h"

class FSocket;
class UStaticMeshComponent;
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

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    TArray<UStaticMeshComponent*> RotorDiscs;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USZTUEnvelopeMonitorComponent* EnvelopeMonitor;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USZTUMavlinkSITLComponent* SITLComponent = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SITL Physics")
    ESZTUMultirotorAirframe Airframe = ESZTUMultirotorAirframe::QuadX;

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

    float ThrottleInput = 0.0f;
    float PitchInput = 0.0f;
    float RollInput = 0.0f;
    float YawInput = 0.0f;

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
};
