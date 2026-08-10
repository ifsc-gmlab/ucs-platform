#include "SZTURoverPawn.h"

#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "SZTUMavlinkSITLComponent.h"
#include "SZTUFlightControllerComponent.h"
#include "SZTUWindFieldPX4Demo.h"
#include "UObject/ConstructorHelpers.h"
#include "Materials/MaterialInstanceDynamic.h"

ASZTURoverPawn::ASZTURoverPawn()
{
    PrimaryActorTick.bCanEverTick = true;

    Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
    RootComponent = Body;
    Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Body->SetSimulatePhysics(false);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> RoverMesh(
        TEXT("/Game/Vehicles/Rover/Meshes/rover_ackermann_complete.rover_ackermann_complete"));
    if (RoverMesh.Succeeded())
    {
        Body->SetStaticMesh(RoverMesh.Object);
        Body->SetRelativeLocation(FVector(0.0f, 0.0f, 10.0f));
    }

    SITLComponent = CreateDefaultSubobject<USZTUMavlinkSITLComponent>(TEXT("SITLComponent"));
    AutoPossessPlayer = EAutoReceiveInput::Player0;
}

void ASZTURoverPawn::BeginPlay()
{
    Super::BeginPlay();

    if (SITLComponent)
    {
        SITLComponent->SetActuatorCount(2);
    }

    if (UStaticMesh* RoverMesh = LoadObject<UStaticMesh>(nullptr,
        TEXT("/Game/Vehicles/Rover/Meshes/rover_ackermann_complete.rover_ackermann_complete")))
    {
        Body->SetStaticMesh(RoverMesh);
        Body->SetRelativeLocation(FVector(0.0f, 0.0f, 10.0f));
    }

    SetActorLocation(FVector(250.0f, 0.0f, 0.0f));
    SetActorRotation(FRotator::ZeroRotator);

    // Green color for rover
    if (UMaterialInterface* BodyMat = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Drone/Materials/M_DroneBodyColor.M_DroneBodyColor")))
    {
        if (UMaterialInstanceDynamic* BodyMID = UMaterialInstanceDynamic::Create(BodyMat, nullptr))
        {
            FLinearColor RoverColor(0.10f, 0.60f, 0.20f, 1.0f); // green #199C33
            BodyMID->SetVectorParameterValue(TEXT("BaseColor"), RoverColor);
            BodyMID->SetVectorParameterValue(TEXT("Color"), RoverColor);
            Body->SetMaterial(0, BodyMID);
        }
    }

    UE_LOG(LogSZTUWindField, Display, TEXT("Rover spawned at %s with Ackermann model"), *GetActorLocation().ToString());
}

void ASZTURoverPawn::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);
    PlayerInputComponent->BindAxis(TEXT("Throttle"), this, &ASZTURoverPawn::SetThrottle);
    PlayerInputComponent->BindAxis(TEXT("Yaw"), this, &ASZTURoverPawn::SetSteering);
}

void ASZTURoverPawn::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (DeltaSeconds <= KINDA_SMALL_NUMBER) { return; }

    TArray<float> Controls;
    const bool bHasSITL = SITLComponent && SITLComponent->IsConnected();
    if (SITLComponent)
    {
        SITLComponent->GetActuatorControls(Controls);
    }

    const float Throttle = FMath::Clamp(bHasSITL && Controls.Num() >= 1 ? Controls[0] : KeyboardThrottle, -1.0f, 1.0f);
    const float Steering = FMath::Clamp(bHasSITL && Controls.Num() >= 2 ? Controls[1] : KeyboardSteering, -1.0f, 1.0f);

    ForwardSpeedMps += Throttle * MaxAccelerationMps2 * DeltaSeconds;
    ForwardSpeedMps *= FMath::Exp(-RollingDragPerSec * DeltaSeconds);
    ForwardSpeedMps = FMath::Clamp(ForwardSpeedMps, -MaxSpeedMps, MaxSpeedMps);
    if (FMath::Abs(Throttle) < 0.01f && FMath::Abs(ForwardSpeedMps) < 0.01f)
    {
        ForwardSpeedMps = 0.0f;
    }

    const float SteeringAngleRad = FMath::DegreesToRadians(MaxSteeringAngleDeg) * Steering;
    YawRateRadS = FMath::Abs(WheelbaseM) > KINDA_SMALL_NUMBER
        ? ForwardSpeedMps / WheelbaseM * FMath::Tan(SteeringAngleRad)
        : 0.0f;

    FRotator Rotation = GetActorRotation();
    Rotation.Roll = 0.0f;
    Rotation.Pitch = 0.0f;
    Rotation.Yaw += FMath::RadiansToDegrees(YawRateRadS * DeltaSeconds);
    SetActorRotation(Rotation);

    LastVelocityMps = VelocityMps;
    VelocityMps = GetActorForwardVector() * ForwardSpeedMps;
    SetActorLocation(GetActorLocation() + VelocityMps * 100.0f * DeltaSeconds);

    if (SITLComponent && bHasSITL)
    {
        const FVector WorldAccelMps2 = (VelocityMps - LastVelocityMps) / DeltaSeconds;
        const FVector SpecificForceBodyUE = GetActorQuat().Inverse().RotateVector(
            WorldAccelMps2 + FVector(0.0f, 0.0f, 9.81f));
        const FVector ImuAccelFRD(SpecificForceBodyUE.X, SpecificForceBodyUE.Y, -SpecificForceBodyUE.Z);
        const FVector VelocityNED(VelocityMps.X, VelocityMps.Y, 0.0f);
        const FVector GyroFRD(0.0f, 0.0f, YawRateRadS);

        SITLComponent->SendDroneState(
            GetActorLocation() / 100.0f,
            VelocityNED,
            GetActorQuat(),
            GyroFRD,
            ImuAccelFRD);
    }

    static int32 FrameCount = 0;
    if (++FrameCount % 60 == 0)
    {
        UE_LOG(LogSZTUWindField, Display,
            TEXT("SITL Rover: connected=%d throttle=%.2f steering=%.2f speed=%.2fm/s yawRate=%.2frad/s position=%s"),
            bHasSITL ? 1 : 0, Throttle, Steering, ForwardSpeedMps, YawRateRadS,
            *GetActorLocation().ToString());
    }
}

void ASZTURoverPawn::SetThrottle(float Value)
{
    KeyboardThrottle = FMath::Clamp(Value, -1.0f, 1.0f);
}

void ASZTURoverPawn::SetSteering(float Value)
{
    KeyboardSteering = FMath::Clamp(Value, -1.0f, 1.0f);
}
