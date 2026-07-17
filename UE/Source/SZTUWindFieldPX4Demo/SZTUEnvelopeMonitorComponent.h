#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SZTUWindFieldActor.h"
#include "SZTUEnvelopeMonitorComponent.generated.h"

USTRUCT(BlueprintType)
struct FSZTUEnvelopeState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly)
    bool bExceeded = false;

    UPROPERTY(BlueprintReadOnly)
    FString Reason;

    UPROPERTY(BlueprintReadOnly)
    float WindSpeedMps = 0.0f;

    UPROPERTY(BlueprintReadOnly)
    float GustDeltaMps = 0.0f;

    UPROPERTY(BlueprintReadOnly)
    float TiltDeg = 0.0f;

    UPROPERTY(BlueprintReadOnly)
    float VerticalSpeedMps = 0.0f;

    UPROPERTY(BlueprintReadOnly)
    float AccelMps2 = 0.0f;
};

UCLASS(ClassGroup=(SZTU), meta=(BlueprintSpawnableComponent))
class SZTUWINDFIELDPX4DEMO_API USZTUEnvelopeMonitorComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    USZTUEnvelopeMonitorComponent();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float MaxWindMps = 18.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float MaxGustDeltaMps = 7.5f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float MaxTiltDeg = 55.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float MaxVerticalSpeedMps = 7.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float MaxAccelMps2 = 35.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Envelope")
    float WarningHoldSeconds = 1.0f;

    UPROPERTY(BlueprintReadOnly, Category = "Envelope")
    FSZTUEnvelopeState LastState;

    UFUNCTION(BlueprintCallable, Category = "Envelope")
    FSZTUEnvelopeState Evaluate(const FSZTUWindSample& WindSample, const FRotator& Attitude, const FVector& VelocityMps, const FVector& AccelMps2, float DeltaSeconds);

private:
    float LastWarningTime = -1000.0f;
};
