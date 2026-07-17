#include "SZTUEnvelopeMonitorComponent.h"
#include "Engine/Engine.h"
#include "SZTUWindFieldPX4Demo.h"

USZTUEnvelopeMonitorComponent::USZTUEnvelopeMonitorComponent()
{
    PrimaryComponentTick.bCanEverTick = false;
}

FSZTUEnvelopeState USZTUEnvelopeMonitorComponent::Evaluate(const FSZTUWindSample& WindSample, const FRotator& Attitude, const FVector& VelocityMps, const FVector& AccelMps2, float DeltaSeconds)
{
    FSZTUEnvelopeState State;
    State.WindSpeedMps = WindSample.SpeedMps;
    State.GustDeltaMps = WindSample.GustDeltaMps;
    State.TiltDeg = FMath::Max(FMath::Abs(Attitude.Roll), FMath::Abs(Attitude.Pitch));
    State.VerticalSpeedMps = FMath::Abs(VelocityMps.Z);
    State.AccelMps2 = AccelMps2.Size();

    TArray<FString> Reasons;
    if (State.WindSpeedMps > MaxWindMps) { Reasons.Add(FString::Printf(TEXT("wind %.1f > %.1f m/s"), State.WindSpeedMps, MaxWindMps)); }
    if (State.GustDeltaMps > MaxGustDeltaMps) { Reasons.Add(FString::Printf(TEXT("gust delta %.1f > %.1f m/s"), State.GustDeltaMps, MaxGustDeltaMps)); }
    if (State.TiltDeg > MaxTiltDeg) { Reasons.Add(FString::Printf(TEXT("tilt %.1f > %.1f deg"), State.TiltDeg, MaxTiltDeg)); }
    if (State.VerticalSpeedMps > MaxVerticalSpeedMps) { Reasons.Add(FString::Printf(TEXT("vertical speed %.1f > %.1f m/s"), State.VerticalSpeedMps, MaxVerticalSpeedMps)); }
    if (State.AccelMps2 > MaxAccelMps2) { Reasons.Add(FString::Printf(TEXT("accel %.1f > %.1f m/s2"), State.AccelMps2, MaxAccelMps2)); }

    State.bExceeded = Reasons.Num() > 0;
    State.Reason = FString::Join(Reasons, TEXT("; "));
    LastState = State;

    const UWorld* World = GetWorld();
    const float Now = World ? World->GetTimeSeconds() : 0.0f;
    if (State.bExceeded && Now - LastWarningTime >= WarningHoldSeconds)
    {
        LastWarningTime = Now;
        UE_LOG(LogSZTUWindField, Warning, TEXT("Flight envelope exceeded: %s"), *State.Reason);
    }

    return State;
}
