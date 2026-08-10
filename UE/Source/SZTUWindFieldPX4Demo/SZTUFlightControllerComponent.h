#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SZTUFlightControllerComponent.generated.h"

// Backend-neutral contract between a vehicle pawn and its flight controller.
// PX4/MAVLink is the default backend today; another SITL or a hardware bridge
// only needs to implement this component contract, without changing vehicle,
// wind-field, mission, telemetry, or web-display logic.
UCLASS(Abstract, BlueprintType, ClassGroup = (SZTU))
class SZTUWINDFIELDPX4DEMO_API USZTUFlightControllerComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    virtual bool IsConnected() const { return false; }
    virtual void GetMotorThrusts(TArray<float>& OutThrusts) const { OutThrusts.Reset(); }
    virtual void GetActuatorControls(TArray<float>& OutControls) const { OutControls.Reset(); }
    virtual void SetActuatorCount(int32 Count) {}

    // Transport configuration is deliberately generic. A TCP implementation
    // can map this to a listen port; a serial/CAN implementation may ignore it.
    virtual void SetTransportPort(int32 Port) {}
    virtual int32 GetTransportPort() const { return 0; }
    virtual void ReinitializeTransport() {}

    virtual void SendDroneState(const FVector& PositionM, const FVector& VelocityMps,
                                const FQuat& Attitude, const FVector& BodyAngularVelocityRadS,
                                const FVector& BodyLinearAccelMps2) {}

    virtual FName GetBackendName() const { return TEXT("None"); }
};
