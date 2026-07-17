#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "SZTURoverPawn.generated.h"

class UStaticMeshComponent;
class USZTUMavlinkSITLComponent;

UCLASS(Blueprintable)
class SZTUWINDFIELDPX4DEMO_API ASZTURoverPawn : public APawn
{
    GENERATED_BODY()

public:
    ASZTURoverPawn();

    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UStaticMeshComponent* Body = nullptr;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USZTUMavlinkSITLComponent* SITLComponent = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rover Physics")
    float WheelbaseM = 0.6f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rover Physics")
    float MaxSteeringAngleDeg = 30.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rover Physics")
    float MaxAccelerationMps2 = 4.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rover Physics")
    float MaxSpeedMps = 8.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Rover Physics")
    float RollingDragPerSec = 0.8f;

private:
    void SetThrottle(float Value);
    void SetSteering(float Value);

    float KeyboardThrottle = 0.0f;
    float KeyboardSteering = 0.0f;
    float ForwardSpeedMps = 0.0f;
    float YawRateRadS = 0.0f;
    FVector VelocityMps = FVector::ZeroVector;
    FVector LastVelocityMps = FVector::ZeroVector;
};
