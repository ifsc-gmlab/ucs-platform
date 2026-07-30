#include "SZTUMavlinkSITLComponent.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "Common/TcpSocketBuilder.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "Misc/DateTime.h"

// 隔离 MAVLink C 头文件（可能和 UE5 宏冲突，先保存再恢复）
THIRD_PARTY_INCLUDES_START
#include "mavlink.h"
THIRD_PARTY_INCLUDES_END

// 把 UE 世界坐标 (X=北, Y=东, Z=上, 单位米) 转成 GPS 经纬度(1e7 整数)。
// 选深圳(22.5°N,114°E)为世界原点；无人机水平位移转为经纬度增量，
// 使 HIL_GPS 经纬度随真实运动变化 —— 否则常量经纬度 + 11m/s 速度积分会让
// EKF2 水平位置创新值发散，触发 "horizontal position unstable" 拒 ARM。
static void SZTUWorldToLatLon(const FVector& WorldPos, int32& OutLat, int32& OutLon)
{
    const double Lat0 = 22.5;
    const double Lon0 = 114.0;
    const double MPerDegLat = 111320.0;
    const double Lat0Rad = FMath::DegreesToRadians(Lat0);
    const double dLat = WorldPos.X / MPerDegLat;
    const double dLon = WorldPos.Y / (MPerDegLat * FMath::Cos(Lat0Rad));
    OutLat = (int32)((Lat0 + dLat) * 1e7);
    OutLon = (int32)((Lon0 + dLon) * 1e7);
}

USZTUMavlinkSITLComponent::USZTUMavlinkSITLComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
    PrimaryComponentTick.bStartWithTickEnabled = true;
    RecvBuffer.SetNumUninitialized(4096);
}

void USZTUMavlinkSITLComponent::BeginPlay()
{
    Super::BeginPlay();
    InitServerSocket();
}

void USZTUMavlinkSITLComponent::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    Super::EndPlay(EndPlayReason);
    if (ClientSocket)
    {
        ClientSocket->Close();
        if (SocketSubsystem) { SocketSubsystem->DestroySocket(ClientSocket); }
        ClientSocket = nullptr;
    }
    if (ServerSocket)
    {
        ServerSocket->Close();
        if (SocketSubsystem) { SocketSubsystem->DestroySocket(ServerSocket); }
        ServerSocket = nullptr;
    }
    if (DebugFile)
    {
        fclose(DebugFile);
        DebugFile = nullptr;
    }
}

void USZTUMavlinkSITLComponent::ReinitServerSocket()
{
    // 关闭旧 socket（如果存在），用当前 ListenPort 重新 bind。
    // 用途：Owner Pawn 在 BeginPlay 改了 ListenPort 后调用。
    if (ClientSocket)
    {
        if (SocketSubsystem) { SocketSubsystem->DestroySocket(ClientSocket); }
        ClientSocket = nullptr;
    }
    if (ServerSocket)
    {
        if (SocketSubsystem) { SocketSubsystem->DestroySocket(ServerSocket); }
        ServerSocket = nullptr;
    }
    UE_LOG(LogTemp, Display, TEXT("[SITL] Reinit server socket with ListenPort=%d"), ListenPort);
    InitServerSocket();
}

void USZTUMavlinkSITLComponent::InitServerSocket()
{
    SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    if (!SocketSubsystem) { UE_LOG(LogTemp, Error, TEXT("[SITL] No socket subsystem")); return; }

    ServerSocket = FTcpSocketBuilder(TEXT("SITL_Server"))
        .AsReusable()
        .WithReceiveBufferSize(64 * 1024)
        .WithSendBufferSize(64 * 1024)
        .BoundToPort(ListenPort)
        .Listening(8);

    if (ServerSocket)
    {
        UE_LOG(LogTemp, Display, TEXT("[SITL] TCP server listening on port %d"), ListenPort);
    }
    else
    {
        UE_LOG(LogTemp, Error, TEXT("[SITL] Failed to bind TCP port %d"), ListenPort);
    }
}

void USZTUMavlinkSITLComponent::CheckForConnections()
{
    if (!ServerSocket) { return; }

    bool bPending = false;
    if (ServerSocket->HasPendingConnection(bPending) && bPending)
    {
        TSharedRef<FInternetAddr> RemoteAddr = SocketSubsystem->CreateInternetAddr();
        FSocket* NewSocket = ServerSocket->Accept(*RemoteAddr, TEXT("SITL_Client"));
        if (NewSocket)
        {
            if (ClientSocket)
            {
                UE_LOG(LogTemp, Warning, TEXT("[SITL] Replacing stale connection with new one from %s"), *RemoteAddr->ToString(true));
                ClientSocket->Close();
                SocketSubsystem->DestroySocket(ClientSocket);
            }
            ClientSocket = NewSocket;
            ClientSocket->SetNonBlocking(true);
            TimeSinceLastRecv = 0.f;
            UE_LOG(LogTemp, Display, TEXT("[SITL] PX4 connected from %s"), *RemoteAddr->ToString(true));
        }
    }
}

void USZTUMavlinkSITLComponent::ProcessIncomingPackets()
{
    if (!ClientSocket) { return; }

    // 只在有 pending data 时才执行 Recv
    // 连接断开通过 Send 失败检测和超时检测来处理
    uint32 PendingDataSize = 0;
    while (ClientSocket->HasPendingData(PendingDataSize) && PendingDataSize > 0)
    {
        int32 BytesRead = 0;
        int32 ToRead = FMath::Min<int32>(PendingDataSize, RecvBuffer.Num());
        if (!ClientSocket->Recv(RecvBuffer.GetData(), ToRead, BytesRead, ESocketReceiveFlags::None))
        {
            // Recv 失败（非阻塞模式下可能只是没有数据），不立即断开
            // 连接断开由超时检测处理
            break;
        }
        if (BytesRead <= 0) { break; }

        // 收到数据，重置超时计时器
        TimeSinceLastRecv = 0.f;

        // 解析 MAVLink 消息
        mavlink_message_t Msg;
        mavlink_status_t Status;
        for (int32 i = 0; i < BytesRead; ++i)
        {
            if (mavlink_parse_char(MAVLINK_COMM_0, RecvBuffer[i], &Msg, &Status))
            {
                if (Msg.msgid == MAVLINK_MSG_ID_HIL_ACTUATOR_CONTROLS)
                {
                    mavlink_hil_actuator_controls_t Controls;
                    mavlink_msg_hil_actuator_controls_decode(&Msg, &Controls);
                    // 用 PX4 的时间戳同步我们的 HIL_SENSOR（lockstep 模式要求）
                    Px4TimeUsec = Controls.time_usec;
                    // lockstep：收到 actuator 后需要立即回复 sensor
                    bForceSendSensor = true;
                    for (int32 Channel = 0; Channel < 16; ++Channel)
                    {
                        ActuatorControls[Channel] = FMath::Clamp(Controls.controls[Channel], -1.f, 1.f);
                    }
                    for (int32 m = 0; m < 8 && m < MotorCount; ++m)
                    {
                        // PX4 HIL_ACTUATOR_CONTROLS.controls[m] 对多旋翼电机就是归一化油门 [0,1]
                        // （0=停转，1=满油门），直接来自 actuator_outputs.output，并非 [-1,1]。
                        // 之前的 controls*0.5+0.5 会把“解锁后 0 油门”误映射成 50% 推力，
                        // 导致无人机凭空悬停/爬升（净升力>重力），PX4 据此判“垂直速度不稳”
                        // 并估出虚假加速度计偏置。现已改为直接用 [0,1] 油门。
                        MotorThrusts[m] = FMath::Clamp(Controls.controls[m], 0.f, 1.f);
                    }
                }
                else if (Msg.msgid == MAVLINK_MSG_ID_HEARTBEAT)
                {
                    mavlink_heartbeat_t Hb;
                    mavlink_msg_heartbeat_decode(&Msg, &Hb);
                    UE_LOG(LogTemp, Verbose, TEXT("[SITL] Heartbeat: type=%d autopilot=%d"), Hb.type, Hb.autopilot);
                }
            }
        }

        // 检查是否还有更多数据
        if (!ClientSocket->HasPendingData(PendingDataSize) || PendingDataSize == 0)
        {
            break;
        }
    }
}

void USZTUMavlinkSITLComponent::SendDroneState(const FVector& PositionM, const FVector& VelocityMps,
                                                const FQuat& Attitude, const FVector& BodyAngularVelocityRadS,
                                                const FVector& BodyLinearAccelMps2)
{
    CachedPositionM = PositionM;
    CachedVelocityMps = VelocityMps;
    CachedAttitude = Attitude;
    CachedAngVelRadS = BodyAngularVelocityRadS;
    CachedLinAccelMps2 = BodyLinearAccelMps2;
}

void USZTUMavlinkSITLComponent::SendScheduledMessages(float DeltaTime)
{
    if (!ClientSocket) { return; }

    SensorTimer += DeltaTime;
    GpsTimer += DeltaTime;
    StateTimer += DeltaTime;

    uint8 SendBuf[MAVLINK_MAX_PACKET_LEN];
    mavlink_message_t Msg;
    int32 BytesSent = 0;
    const uint8_t SysId = 1;    // 仿真器 system ID
    const uint8_t CompId = 1;  // component ID

    // 维护仿真自己的单调时钟：基于真实墙钟时间，按真实帧间隔(DeltaTime)推进。
    // 关键修复：绝不能把 TimeUsec 直接设为 PX4 的 time_usec —— 两者 epoch 不同
    // （UE 用 unix 微秒 ~1.7e15，PX4 的 hrt 是启动相对微秒 ~1e8）。
    // 旧实现在收到 HIL_ACTUATOR_CONTROLS 后把 TimeUsec 从 1.7e15 硬跳到 1e8，
    // 造成 HIL_SENSOR 时间戳巨幅回退，PX4 把后续所有 sensor 判为“时间倒流”而丢弃，
    // 于是 PX4 只处理第一条 sensor、永不回发 actuator（锁步死锁，UE 30s 超时断开）。
    // 2026-07-21：改为成员变量（非 static），双机并行时每实例独立时钟，避免时序互扰。
    if (TimeUsec == 0)
    {
        TimeUsec = (uint64_t)FDateTime::UtcNow().ToUnixTimestamp() * 1000000ULL;
    }
    // 按真实帧间隔推进（秒→微秒），保证单调且实时节奏
    TimeUsec += (uint64_t)(DeltaTime * 1000000.0);

    // HIL_STATE_QUATERNION (~50Hz)：位置+速度+姿态+角速度
    if (StateTimer >= 0.02f)
    {
        StateTimer = 0.f;
        // UE 机体/世界是 Z-up(FRU / Z上)，PX4 HIL 要求 FRD / Z-down(NED)。
        // 角速度/加速度/速度/磁罗盘均已按 C=diag(1,1,-1)（仅翻转 Z 轴）变换到 FRD/NED，
        // 但姿态四元数此前是“裸”UE 四元数，与上面向量坐标系不一致 → PX4 姿态控制
        // 正反馈、无人机俯仰翻跟头、无法起飞。这里用同一 C 做 R_frd = C·R·C，
        // 通过矩阵数值转换（避免手推符号出错），得到与向量一致的 FRD 姿态四元数。
        const FMatrix Rue = FRotationMatrix::Make(CachedAttitude.Rotator()); // body -> UE-world(Z上)
        const FMatrix C = FMatrix(
            FPlane(1.f, 0.f, 0.f, 0.f),
            FPlane(0.f, 1.f, 0.f, 0.f),
            FPlane(0.f, 0.f, -1.f, 0.f),
            FPlane(0.f, 0.f, 0.f, 1.f));
        const FQuat Q = FQuat(C * Rue * C); // 与向量同一 Z-flip 约定
        float QuatArr[4] = { Q.W, Q.X, Q.Y, Q.Z };
        // === HIL 调试日志（CSV）：记录发往 PX4 的四元数/角速度/加速度/电机/高度 ===
        {
            if (!DebugFile)
            {
                // 双机并行：按 ListenPort 区分文件名，避免两个实例写同一个文件
                const FString DbgPath = FString::Printf(TEXT("C:/tmp/hil_debug_%d.csv"), ListenPort);
                DebugFile = fopen(TCHAR_TO_ANSI(*DbgPath), "w");
                if (DebugFile) fprintf(DebugFile, "t,w,x,y,z,gx,gy,gz,ax,ay,az,m0,m1,m2,m3,posz,craw_qw,craw_qx,craw_qy,craw_qz\n");
            }
            if (DebugFile)
            {
                fprintf(DebugFile,
                    "%llu,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f,%.3f,%.3f,%.4f,%.4f,%.4f,%.4f\n",
                    TimeUsec, Q.W, Q.X, Q.Y, Q.Z,
                    CachedAngVelRadS.X, CachedAngVelRadS.Y, CachedAngVelRadS.Z,
                    CachedLinAccelMps2.X, CachedLinAccelMps2.Y, CachedLinAccelMps2.Z,
                    MotorThrusts[0], MotorThrusts[1], MotorThrusts[2], MotorThrusts[3],
                    CachedPositionM.Z,
                    CachedAttitude.W, CachedAttitude.X, CachedAttitude.Y, CachedAttitude.Z);
                fflush(DebugFile);
            }
        }
        int32 HStateLat = 0, HStateLon = 0;
        SZTUWorldToLatLon(CachedPositionM, HStateLat, HStateLon);
        mavlink_msg_hil_state_quaternion_pack(
            SysId, CompId, &Msg,
            TimeUsec,                            // time_usec
            QuatArr,                             // attitude_quaternion[4]
            CachedAngVelRadS.X, CachedAngVelRadS.Y, CachedAngVelRadS.Z,  // roll/pitch/yawspeed (rad/s)
            HStateLat,                           // lat (随世界X=北 变化)
            HStateLon,                           // lon (随世界Y=东 变化)
            (int32_t)(CachedPositionM.Z * 1000.f), // alt (mm)
            (int16_t)(CachedVelocityMps.X * 100.f), // vx (cm/s)
            (int16_t)(CachedVelocityMps.Y * 100.f),  // vy
            (int16_t)(CachedVelocityMps.Z * 100.f), // vz
            0, 0,                                  // ind_airspeed, true_airspeed
            (int16_t)(CachedLinAccelMps2.X * 1000.f / 9.81f), // xacc (mG)
            (int16_t)(CachedLinAccelMps2.Y * 1000.f / 9.81f), // yacc
            (int16_t)(CachedLinAccelMps2.Z * 1000.f / 9.81f)); // zacc
        uint16_t Len = mavlink_msg_to_send_buffer(SendBuf, &Msg);
        ClientSocket->Send(SendBuf, Len, BytesSent);

        // Feed the exact UE pose/velocity as external-vision odometry. HIL_GPS
        // does not carry a configurable speed-accuracy field, so EKF2 otherwise
        // permits visible low-frequency Z-velocity drift even with perfect UE
        // ground truth. The regular HIL sensor and GPS streams remain enabled.
        float PoseCovariance[21] = {};
        PoseCovariance[0] = 0.0025f;
        PoseCovariance[6] = 0.0025f;
        PoseCovariance[11] = 0.0025f;
        PoseCovariance[15] = 0.01f;
        PoseCovariance[18] = 0.01f;
        PoseCovariance[20] = 0.01f;
        float VelocityCovariance[21] = {};
        VelocityCovariance[0] = 0.0025f;
        VelocityCovariance[6] = 0.0025f;
        VelocityCovariance[11] = 0.0025f;
        VelocityCovariance[15] = 0.01f;
        VelocityCovariance[18] = 0.01f;
        VelocityCovariance[20] = 0.01f;
        if (!bOdometryOriginInitialized)
        {
            OdometryOriginM = CachedPositionM;
            bOdometryOriginInitialized = true;
        }
        const FVector LocalPositionM = CachedPositionM - OdometryOriginM;
        mavlink_msg_odometry_pack(
            SysId, CompId, &Msg,
            TimeUsec,
            MAV_FRAME_LOCAL_NED,
            MAV_FRAME_LOCAL_NED,
            LocalPositionM.X,
            LocalPositionM.Y,
            -LocalPositionM.Z,
            QuatArr,
            CachedVelocityMps.X,
            CachedVelocityMps.Y,
            CachedVelocityMps.Z,
            CachedAngVelRadS.X,
            CachedAngVelRadS.Y,
            CachedAngVelRadS.Z,
            PoseCovariance,
            VelocityCovariance,
            0,
            MAV_ESTIMATOR_TYPE_VISION,
            100);
        Len = mavlink_msg_to_send_buffer(SendBuf, &Msg);
        ClientSocket->Send(SendBuf, Len, BytesSent);
    }

    // HIL_SENSOR：定时发送(250Hz) 或 lockstep 强制发送（收到 actuator 后立即回复）
    if (SensorTimer >= 0.004f || bForceSendSensor)
    {
        SensorTimer = 0.f;
        bForceSendSensor = false;
        // MAVLink HIL_SENSOR fields_updated 位定义（每个轴一位，不是每个传感器）：
        // bit0-2: XACCEL/YACCEL/ZACCEL
        // bit3-5: XGYRO/YGYRO/ZGYRO
        // bit6-8: XMAG/YMAG/ZMAG
        // bit9: ABS_PRESSURE, bit10: DIFF_PRESSURE, bit11: PRESSURE_ALT, bit12: TEMPERATURE
        const uint16_t AllFields =
            (1 << 0)  // XACCEL
          | (1 << 1)  // YACCEL
          | (1 << 2)  // ZACCEL
          | (1 << 3)  // XGYRO
          | (1 << 4)  // YGYRO
          | (1 << 5)  // ZGYRO
          | (1 << 6)  // XMAG
          | (1 << 7)  // YMAG
          | (1 << 8)  // ZMAG
          | (1 << 9)  // ABS_PRESSURE
          | (1 << 11) // PRESSURE_ALT
          | (1 << 12);// TEMPERATURE
        // 地球磁场：在世界系(UE: X前 Y右 Z上)取一个固定场向量，按当前机体姿态旋到机体系，
        // 再对 Z 取反转成 FRD(Z下)，与 accel/gyro 同一约定。
        // 这样任意姿态下机体磁场都随姿态一致旋转，EKF2 才能稳定对齐 yaw(航向)。
        // 关键：该向量必须是“深圳(22.5°N,114°E)真实地磁场在世界系的分量”，否则 EKF2 的
        // 三轴磁融合创新值(mag_ratio)会爆掉、航向永远无法对齐(YAW_ALIGN=0)，
        // 进而级联触发“heading estimate invalid / High Accel Bias / GPS Vertical Drift”。
        //   - 北半球地磁场指向“北 + 下”：水平分量朝北(≈+X)，垂直分量向下(世界系 Z 应为负)。
        //   - 深圳 WMM 近似：磁偏角≈-2.5°(偏西)、磁倾角≈42°、总场强≈0.48G。
        //     世界系(NED 约定 X=N,Y=E,Z上=-D)地磁场 ≈ (0.36, -0.016, -0.32) Gauss。
        //     之前写成 (0.3,0,0.25) 把垂直分量写成“向上”，导致倾角反向、mag_ratio≈1.9、航向无效。
        const FVector EarthFieldWorld(0.36f, -0.016f, -0.32f);
        const FVector MagBodyUE = CachedAttitude.Inverse().RotateVector(EarthFieldWorld);
        // 加微噪声：PX4 DataValidator 在“连续收到完全相同的值超过阈值”时会置 ERROR_FLAG_STALE_DATA，
        // 导致 BARO/MAG STALE。真实传感器本就有噪声，加 ±3mG 可破解这个判定且更接近真实。
        const float MagX = MagBodyUE.X + FMath::FRandRange(-0.003f, 0.003f);
        const float MagY = MagBodyUE.Y + FMath::FRandRange(-0.003f, 0.003f);
        const float MagZ = -MagBodyUE.Z + FMath::FRandRange(-0.003f, 0.003f);
        // 气压随高度变化(barometric formula) + 微噪声：高度不变时压力恒定同样会触发 STALE，噪声破解之。
        // 0.05 hPa 约等于 0.42 m 高度误差，会让 EKF 估计和高度环出现肉眼可见的低频起伏。
        // 保留约 4 cm 等效噪声即可避免 stale，同时不会主导悬停高度。
        // 注意单位：MAVLink HIL_SENSOR 的 abs_pressure 就是 hPa，PX4 内部 *100 转 Pa。
        const float BaroHpa = 1013.25f * FMath::Exp(-CachedPositionM.Z / 8435.0f)
                              + FMath::FRandRange(-0.005f, 0.005f);
        mavlink_msg_hil_sensor_pack(
            SysId, CompId, &Msg,
            TimeUsec,
            CachedLinAccelMps2.X + FMath::FRandRange(-0.01f, 0.01f),
            CachedLinAccelMps2.Y + FMath::FRandRange(-0.01f, 0.01f),
            CachedLinAccelMps2.Z + FMath::FRandRange(-0.01f, 0.01f),
            CachedAngVelRadS.X + FMath::FRandRange(-0.001f, 0.001f),
            CachedAngVelRadS.Y + FMath::FRandRange(-0.001f, 0.001f),
            CachedAngVelRadS.Z + FMath::FRandRange(-0.001f, 0.001f),
            MagX, MagY, MagZ,    // xmag/ymag/zmag (gauss) 机体坐标系地磁场（随姿态旋转）
            BaroHpa,              // abs_pressure (hPa)
            0.f,                 // diff_pressure (Pa)
            CachedPositionM.Z,   // pressure_alt (m)
            25.f,                // temperature (degC)
            AllFields, 0);  // all fields, id=0
        uint16_t Len = mavlink_msg_to_send_buffer(SendBuf, &Msg);
        ClientSocket->Send(SendBuf, Len, BytesSent);
    }

    // HIL_GPS (~10Hz)
    if (GpsTimer >= 0.1f)
    {
        GpsTimer = 0.f;
        int32 GpsLat = 0, GpsLon = 0;
        SZTUWorldToLatLon(CachedPositionM, GpsLat, GpsLon);
        mavlink_msg_hil_gps_pack(
            SysId, CompId, &Msg,
            TimeUsec,                          // usec time
            3,                              // fix type (3D)
            GpsLat,                          // lat (随世界X=北 变化)
            GpsLon,                          // lon (随世界Y=东 变化)
            (int32_t)(CachedPositionM.Z * 1000.f), // alt (mm)
            100,                            // eph (cm)
            10,                             // epv (cm): UE altitude is simulation ground truth
            (uint16_t)(FMath::Sqrt(CachedVelocityMps.X*CachedVelocityMps.X + CachedVelocityMps.Y*CachedVelocityMps.Y) * 100), // vel (cm/s ground speed)
            (int16_t)(CachedVelocityMps.X * 100), // vn (cm/s)
            (int16_t)(CachedVelocityMps.Y * 100), // ve
            (int16_t)(CachedVelocityMps.Z * 100), // vd
            0,                              // cog
            10,                             // satellites_visible
            0, 0);                          // id, yaw
        uint16_t Len = mavlink_msg_to_send_buffer(SendBuf, &Msg);
        ClientSocket->Send(SendBuf, Len, BytesSent);
    }
}

void USZTUMavlinkSITLComponent::GetMotorThrusts(TArray<float>& OutThrusts) const
{
    OutThrusts.SetNum(MotorCount);
    for (int32 i = 0; i < MotorCount; ++i)
    {
        OutThrusts[i] = MotorThrusts[i];
    }
}

void USZTUMavlinkSITLComponent::GetActuatorControls(TArray<float>& OutControls) const
{
    OutControls.SetNum(ActuatorCount);
    for (int32 i = 0; i < ActuatorCount; ++i)
    {
        OutControls[i] = ActuatorControls[i];
    }
}

void USZTUMavlinkSITLComponent::SetActuatorCount(int32 Count)
{
    ActuatorCount = FMath::Clamp(Count, 1, 16);
    MotorCount = FMath::Clamp(Count, 1, 8);
}

void USZTUMavlinkSITLComponent::TickComponent(float DeltaTime, ELevelTick TickType,
                                                FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

    // 超时检测：如果连接存在但长时间没收到数据，认为断开
    if (ClientSocket)
    {
        TimeSinceLastRecv += DeltaTime;
        if (TimeSinceLastRecv > ConnectionTimeoutSec)
        {
            UE_LOG(LogTemp, Warning, TEXT("[SITL] Connection timeout (%.1fs without data), disconnecting"), TimeSinceLastRecv);
            ClientSocket->Close();
            SocketSubsystem->DestroySocket(ClientSocket);
            ClientSocket = nullptr;
            TimeSinceLastRecv = 0.f;
        }
    }

    CheckForConnections();
    ProcessIncomingPackets();
    SendScheduledMessages(DeltaTime);
}
