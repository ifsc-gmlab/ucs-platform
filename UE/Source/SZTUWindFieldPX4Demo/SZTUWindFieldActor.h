#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SZTUWindFieldActor.generated.h"

class UInstancedStaticMeshComponent;
class USceneComponent;

USTRUCT(BlueprintType)
struct FSZTUWindSample
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly)
    FVector VelocityMps = FVector::ZeroVector;

    UPROPERTY(BlueprintReadOnly)
    float SpeedMps = 0.0f;

    UPROPERTY(BlueprintReadOnly)
    float GustDeltaMps = 0.0f;
};

UCLASS(Blueprintable)
class SZTUWINDFIELDPX4DEMO_API ASZTUWindFieldActor : public AActor
{
    GENERATED_BODY()

public:
    ASZTUWindFieldActor();

    virtual void OnConstruction(const FTransform& Transform) override;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
#if WITH_EDITOR
    virtual bool ShouldTickIfViewportsOnly() const override;
#endif

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USceneComponent* SceneRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UInstancedStaticMeshComponent* FanCells;

    UFUNCTION(BlueprintCallable, Category = "SZTU Wind Field")
    FSZTUWindSample SampleWindAtWorldLocation(const FVector& WorldLocation) const;

    UFUNCTION(BlueprintCallable, Category = "SZTU Wind Field")
    void SetRuntimeWindSpeedMps(float WindSpeedMps);

    UFUNCTION(BlueprintPure, Category = "SZTU Wind Field")
    float GetRuntimeWindSpeedMps() const;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SZTU Reference")
    int32 FanColumns = 80;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SZTU Reference")
    int32 FanRows = 40;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SZTU Reference")
    float ArrayWidthM = 6.5f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SZTU Reference")
    float ArrayHeightM = 3.25f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    FVector WindAxis = FVector(1.0f, 0.0f, 0.0f);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float BaseWindMps = 12.86f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float GustAmplitudeMps = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float GustFrequencyHz = 0.0142857f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float VerticalShearMpsPerM = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float LateralGradientMpsPerM = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float TurbulenceAmplitudeMps = 0.3f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float VortexStrengthMps = 0.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Model")
    float VortexRadiusM = 1.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization")
    int32 VisualizationStride = 8;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization")
    float ArrowScaleCmPerMps = 18.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization")
    bool bShowFanArray = true;

    // 默认关闭：编辑器视口(非 PIE)下不绘制风场箭头，避免几千条 debug 线拖垮视口导致卡死。
    // 需要可视化风场时再手动勾选。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization")
    bool bShowWindArrows = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization", meta = (ClampMin = "0.1", ClampMax = "1.0"))
    float FanCellFill = 0.78f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Visualization", meta = (ClampMin = "1.0", ClampMax = "50.0"))
    float FanCellDepthCm = 7.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    bool bUseFAWG05MeasuredCurve = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    bool bUseFAWG05CycleProgram = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    bool bUseFAWG05VerticalCutProfile = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    float FAWG05FanPercent = 80.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    float FAWG05MeasurementDistanceM = 3.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "FAWG-05 Calibration")
    float FAWG05WallBottomClearanceM = 0.6f;

private:
    void DrawWindField() const;
    FVector LocalMetersToWorld(const FVector& LocalMeters) const;
    float EvaluateFAWG05SteadyWindMps(float FanPercent, float DistanceM) const;
    float EvaluateFAWG05CycleFanPercent(float TimeSeconds) const;
    float EvaluateFAWG05VerticalCutMps(float HeightAboveGroundM) const;
};
