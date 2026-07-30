#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SZTUMavlinkSITLComponent.generated.h"

// MAVLink SITL 仿真器接口组件
// UE5 作为 TCP server 监听端口，PX4 SITL 作为 client 连接
// 发送：HIL_SENSOR / HIL_GPS / HIL_STATE_QUATERNION / ODOMETRY（传感器+状态）
// 接收：HIL_ACTUATOR_CONTROLS（电机输出）
UCLASS(ClassGroup = (SZTU), meta = (BlueprintSpawnableComponent))
class SZTUWINDFIELDPX4DEMO_API USZTUMavlinkSITLComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    USZTUMavlinkSITLComponent();

    // PX4 SITL 连接的 TCP 端口（PX4 默认连接 localhost:4560）
    UPROPERTY(EditAnywhere, Category = "SITL")
    int32 ListenPort = 4560;

    // 是否已连接 PX4 SITL
    UFUNCTION(BlueprintCallable, Category = "SITL")
    bool IsConnected() const { return ClientSocket != nullptr; }

    // 获取最新电机输出（标准化推力 0~1，按电机索引）
    UFUNCTION(BlueprintCallable, Category = "SITL")
    void GetMotorThrusts(TArray<float>& OutThrusts) const;

    // Raw HIL actuator values. Multirotor motors normally use [0,1], while
    // reversible rover motors and steering servos use [-1,1].
    UFUNCTION(BlueprintCallable, Category = "SITL")
    void GetActuatorControls(TArray<float>& OutControls) const;

    UFUNCTION(BlueprintCallable, Category = "SITL")
    void SetActuatorCount(int32 Count);

    // 重新初始化 TCP server socket（用当前 ListenPort）。
    // 用途：Owner Pawn 在 BeginPlay 改了 ListenPort 之后调一次让 socket 重新 bind。
    UFUNCTION(BlueprintCallable, Category = "SITL")
    void ReinitServerSocket();

    // 发送无人机状态给 PX4（位置/速度/姿态/角速度，单位：米、米/秒、弧度）
    void SendDroneState(const FVector& PositionM, const FVector& VelocityMps,
                        const FQuat& Attitude, const FVector& BodyAngularVelocityRadS,
                        const FVector& BodyLinearAccelMps2);

    virtual void TickComponent(float DeltaTime, ELevelTick TickType,
                               FActorComponentTickFunction* ThisTickFunction) override;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    void InitServerSocket();
    void CheckForConnections();
    void ProcessIncomingPackets();
    void SendScheduledMessages(float DeltaTime);

    // Socket
    class FSocket* ServerSocket = nullptr;
    class FSocket* ClientSocket = nullptr;
    class ISocketSubsystem* SocketSubsystem = nullptr;

    // 电机输出缓存（最多8路，iris用前4路）
    float MotorThrusts[8] = {0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f};
    int32 MotorCount = 4;
    float ActuatorControls[16] = {};
    int32 ActuatorCount = 4;

    // 传感器发送计时器（PX4 期望 ~250-500Hz 传感器、~50Hz GPS、~100Hz 状态）
    float SensorTimer = 0.f;      // HIL_SENSOR 间隔
    float GpsTimer = 0.f;        // HIL_GPS 间隔
    float StateTimer = 0.f;       // HIL_STATE 间隔

    // 缓存的状态（由 SendDroneState 写入，由 SendScheduledMessages 读取）
    FVector CachedPositionM = FVector::ZeroVector;
    FVector CachedVelocityMps = FVector::ZeroVector;
    FQuat CachedAttitude = FQuat::Identity;
    FVector CachedAngVelRadS = FVector::ZeroVector;
    FVector CachedLinAccelMps2 = FVector::ZeroVector;
    FVector OdometryOriginM = FVector::ZeroVector;
    bool bOdometryOriginInitialized = false;

    // 接收缓冲
    TArray<uint8> RecvBuffer;

    // PX4 发送 HIL_ACTUATOR_CONTROLS 时的时间戳（用于 lockstep 同步）
    uint64_t Px4TimeUsec = 0;

    // 仿真单调时钟（每个 SITLComponent 实例独立，避免双机共享 static 导致时序混乱）
    uint64_t TimeUsec = 0;

    // lockstep：收到 HIL_ACTUATOR_CONTROLS 后需要立即回复 HIL_SENSOR
    bool bForceSendSensor = false;

    // 连接超时检测：超过此时间没收到数据，认为连接断开
    float TimeSinceLastRecv = 0.f;
    UPROPERTY(EditAnywhere, Category = "SITL")
    float ConnectionTimeoutSec = 30.f;

    // HIL 调试 CSV 文件句柄（每实例独立，避免双机写同一个文件冲突）
    FILE* DebugFile = nullptr;
};
