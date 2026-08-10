#include "SZTUWindFieldActor.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SceneComponent.h"
#include "DrawDebugHelpers.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInterface.h"
#include "UObject/ConstructorHelpers.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "SZTUWindFieldPX4Demo.h"

namespace
{
float Interpolate1D(const float* X, const float* Y, int32 Count, float Query)
{
    if (Count <= 0) { return 0.0f; }
    if (Count == 1 || Query <= X[0]) { return Y[0]; }
    if (Query >= X[Count - 1]) { return Y[Count - 1]; }

    for (int32 Index = 0; Index < Count - 1; ++Index)
    {
        if (Query <= X[Index + 1])
        {
            const float Range = FMath::Max(KINDA_SMALL_NUMBER, X[Index + 1] - X[Index]);
            const float Alpha = (Query - X[Index]) / Range;
            return FMath::Lerp(Y[Index], Y[Index + 1], Alpha);
        }
    }

    return Y[Count - 1];
}
}

ASZTUWindFieldActor::ASZTUWindFieldActor()
{
    PrimaryActorTick.bCanEverTick = true;
    SetActorTickInterval(0.05f);

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
    RootComponent = SceneRoot;

    FanCells = CreateDefaultSubobject<UInstancedStaticMeshComponent>(TEXT("FanCells"));
    FanCells->SetupAttachment(SceneRoot);
    FanCells->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    FanCells->SetCastShadow(false);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeMesh(TEXT("/Engine/BasicShapes/Cube.Cube"));
    if (CubeMesh.Succeeded())
    {
        FanCells->SetStaticMesh(CubeMesh.Object);
    }

    // 风扇：M_FanColor（青色）
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> FanMat(TEXT("/Game/Drone/Materials/M_FanColor.M_FanColor"));
    if (FanMat.Succeeded())
    {
        FanCells->SetMaterial(0, FanMat.Object);
        UE_LOG(LogSZTUWindField, Display, TEXT("Fan material: M_FanColor (cyan)"));
    }
}

void ASZTUWindFieldActor::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);

    if (!FanCells)
    {
        return;
    }

    FanCells->ClearInstances();
    if (!bShowFanArray || FanColumns <= 0 || FanRows <= 0 || ArrayWidthM <= 0.0f || ArrayHeightM <= 0.0f)
    {
        return;
    }

    const float WidthCm = ArrayWidthM * 100.0f;
    const float HeightCm = ArrayHeightM * 100.0f;
    const float HalfW = WidthCm * 0.5f;
    const float HalfH = HeightCm * 0.5f;
    const float Fill = FMath::Clamp(FanCellFill, 0.1f, 1.0f);
    const float CellY = WidthCm / float(FanColumns) * Fill;
    const float CellZ = HeightCm / float(FanRows) * Fill;
    const float CellX = FMath::Max(1.0f, FanCellDepthCm);

    for (int32 Row = 0; Row < FanRows; ++Row)
    {
        for (int32 Col = 0; Col < FanColumns; ++Col)
        {
            const float Y = -HalfW + (float(Col) + 0.5f) * WidthCm / float(FanColumns);
            const float Z = -HalfH + (float(Row) + 0.5f) * HeightCm / float(FanRows);
            FanCells->AddInstance(FTransform(
                FRotator::ZeroRotator,
                FVector(0.0f, Y, Z),
                FVector(CellX / 100.0f, CellY / 100.0f, CellZ / 100.0f)));
        }
    }
}

void ASZTUWindFieldActor::BeginPlay()
{
    Super::BeginPlay();
    float RequestedWindMps = 0.0f;
    if (FParse::Value(FCommandLine::Get(), TEXT("WindMps="), RequestedWindMps))
    {
        BaseWindMps = FMath::Clamp(RequestedWindMps, 0.0f, 30.0f);
        bUseFAWG05MeasuredCurve = false;
        bUseFAWG05CycleProgram = false;
        bUseFAWG05VerticalCutProfile = false;
        UE_LOG(LogSZTUWindField, Display, TEXT("Wind speed override: %.2f m/s via -WindMps"), BaseWindMps);
    }
    // 命令行 -ShowWindArrows 可在不修改地图默认值(false)的情况下强制显示风场箭头，用于验收/演示。
    if (FParse::Param(FCommandLine::Get(), TEXT("ShowWindArrows")))
    {
        bShowWindArrows = true;
        UE_LOG(LogSZTUWindField, Display, TEXT("bShowWindArrows forced ON via -ShowWindArrows"));
    }
    UE_LOG(LogSZTUWindField, Display, TEXT("SZTU wind field: %d fans (%d x %d), %.2fm x %.2fm"), FanColumns * FanRows, FanColumns, FanRows, ArrayWidthM, ArrayHeightM);
}

void ASZTUWindFieldActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    DrawWindField();
}

void ASZTUWindFieldActor::SetRuntimeWindSpeedMps(float WindSpeedMps)
{
    BaseWindMps = FMath::Clamp(WindSpeedMps, 0.0f, 30.0f);
    bUseFAWG05MeasuredCurve = false;
    bUseFAWG05CycleProgram = false;
    bUseFAWG05VerticalCutProfile = false;
    if (FMath::IsNearlyZero(BaseWindMps))
    {
        // A zero-wind acceptance run must not retain positive Perlin noise,
        // gust, shear, gradient, or vortex residuals from the map preset.
        GustAmplitudeMps = 0.0f;
        VerticalShearMpsPerM = 0.0f;
        LateralGradientMpsPerM = 0.0f;
        TurbulenceAmplitudeMps = 0.0f;
        VortexStrengthMps = 0.0f;
    }
    UE_LOG(LogSZTUWindField, Display, TEXT("Runtime wind speed set to %.2f m/s"), BaseWindMps);
}

float ASZTUWindFieldActor::GetRuntimeWindSpeedMps() const
{
    const float Time = GetWorld() ? GetWorld()->GetTimeSeconds() : 0.0f;
    const float FanPercent = bUseFAWG05CycleProgram ? EvaluateFAWG05CycleFanPercent(Time) : FAWG05FanPercent;
    return bUseFAWG05MeasuredCurve
        ? EvaluateFAWG05SteadyWindMps(FanPercent, FAWG05MeasurementDistanceM)
        : BaseWindMps;
}

#if WITH_EDITOR
bool ASZTUWindFieldActor::ShouldTickIfViewportsOnly() const
{
    // 只有在需要可视化箭头时才在编辑器视口(非 PIE)每帧 tick，否则不 tick，避免卡死
    return bShowWindArrows;
}
#endif

FSZTUWindSample ASZTUWindFieldActor::SampleWindAtWorldLocation(const FVector& WorldLocation) const
{
    const float Time = GetWorld() ? GetWorld()->GetTimeSeconds() : 0.0f;
    const FVector LocalM = GetActorTransform().InverseTransformPosition(WorldLocation) / 100.0f;
    const FVector Axis = WindAxis.IsNearlyZero() ? FVector::ForwardVector : WindAxis.GetSafeNormal();

    const float EffectiveFanPercent = bUseFAWG05CycleProgram ? EvaluateFAWG05CycleFanPercent(Time) : FAWG05FanPercent;
    float EffectiveBaseWindMps = bUseFAWG05MeasuredCurve ? EvaluateFAWG05SteadyWindMps(EffectiveFanPercent, FAWG05MeasurementDistanceM) : BaseWindMps;
    if (bUseFAWG05VerticalCutProfile)
    {
        const float HeightAboveGroundM = LocalM.Z + FAWG05WallBottomClearanceM + ArrayHeightM * 0.5f;
        EffectiveBaseWindMps = EvaluateFAWG05VerticalCutMps(HeightAboveGroundM);
    }

    const float Gust = GustAmplitudeMps * FMath::Sin(2.0f * PI * GustFrequencyHz * Time + LocalM.Y * 0.7f);
    const float Shear = bUseFAWG05VerticalCutProfile ? 0.0f : VerticalShearMpsPerM * LocalM.Z;
    const float Gradient = LateralGradientMpsPerM * LocalM.Y;

    const FVector NoiseInput(LocalM.X * 0.6f + Time * 0.3f, LocalM.Y * 0.6f, LocalM.Z * 0.6f);
    const float Noise = FMath::PerlinNoise3D(NoiseInput) * TurbulenceAmplitudeMps;

    FVector Velocity = Axis * FMath::Max(0.0f, EffectiveBaseWindMps + Gust + Shear + Gradient + Noise);

    const FVector VortexPlane(0.0f, LocalM.Y, LocalM.Z);
    const float Radius = FMath::Max(0.001f, FMath::Sqrt(VortexPlane.Y * VortexPlane.Y + VortexPlane.Z * VortexPlane.Z));
    const float Falloff = FMath::Exp(-(Radius * Radius) / FMath::Max(0.001f, VortexRadiusM * VortexRadiusM));
    const FVector Tangent(0.0f, -VortexPlane.Z, VortexPlane.Y);
    Velocity += Tangent.GetSafeNormal() * VortexStrengthMps * Falloff;

    FSZTUWindSample Sample;
    Sample.VelocityMps = Velocity;
    Sample.SpeedMps = Velocity.Size();
    const float ProgramDeltaMps = bUseFAWG05CycleProgram ? FMath::Abs(EffectiveBaseWindMps - EvaluateFAWG05SteadyWindMps(FAWG05FanPercent, FAWG05MeasurementDistanceM)) : 0.0f;
    Sample.GustDeltaMps = ProgramDeltaMps + FMath::Abs(Gust) + FMath::Abs(Noise);
    return Sample;
}

float ASZTUWindFieldActor::EvaluateFAWG05SteadyWindMps(float FanPercent, float DistanceM) const
{
    static const float PercentTable[] = { 10.0f, 20.0f, 30.0f, 40.0f, 50.0f, 60.0f, 70.0f, 78.0f, 80.0f, 90.0f, 100.0f };
    static const float DistanceTable[] = { 1.5f, 3.0f, 4.5f, 8.0f };
    static const float SpeedTable[4][11] =
    {
        { 1.43f, 3.11f, 4.66f, 6.24f, 7.83f, 9.47f, 11.08f, 12.50f, 12.71f, 14.23f, 15.14f },
        { 1.41f, 3.06f, 4.67f, 5.47f, 7.84f, 9.48f, 11.89f, 12.50f, 12.86f, 14.36f, 14.97f },
        { 1.44f, 3.11f, 4.73f, 6.32f, 7.91f, 9.58f, 11.34f, 12.66f, 13.06f, 14.65f, 15.14f },
        { 1.42f, 3.15f, 4.76f, 6.36f, 7.97f, 9.63f, 11.36f, 12.72f, 13.09f, 14.66f, 15.16f }
    };

    float SpeedByDistance[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
    for (int32 DistanceIndex = 0; DistanceIndex < 4; ++DistanceIndex)
    {
        SpeedByDistance[DistanceIndex] = Interpolate1D(PercentTable, SpeedTable[DistanceIndex], 11, FanPercent);
    }

    return Interpolate1D(DistanceTable, SpeedByDistance, 4, DistanceM);
}

float ASZTUWindFieldActor::EvaluateFAWG05CycleFanPercent(float TimeSeconds) const
{
    constexpr float LowPercent = 30.0f;
    constexpr float HighPercent = 80.0f;
    constexpr float LowHoldSeconds = 5.0f;
    constexpr float RampUpSeconds = 30.0f;
    constexpr float HighHoldSeconds = 5.0f;
    constexpr float RampDownSeconds = 30.0f;
    constexpr float PeriodSeconds = LowHoldSeconds + RampUpSeconds + HighHoldSeconds + RampDownSeconds;

    const float Phase = FMath::Fmod(FMath::Max(0.0f, TimeSeconds), PeriodSeconds);
    if (Phase < LowHoldSeconds) { return LowPercent; }
    if (Phase < LowHoldSeconds + RampUpSeconds)
    {
        return FMath::Lerp(LowPercent, HighPercent, (Phase - LowHoldSeconds) / RampUpSeconds);
    }
    if (Phase < LowHoldSeconds + RampUpSeconds + HighHoldSeconds) { return HighPercent; }
    return FMath::Lerp(HighPercent, LowPercent, (Phase - LowHoldSeconds - RampUpSeconds - HighHoldSeconds) / RampDownSeconds);
}

float ASZTUWindFieldActor::EvaluateFAWG05VerticalCutMps(float HeightAboveGroundM) const
{
    static const float HeightTable[] = { 0.925f, 1.250f, 1.575f, 1.900f, 2.225f, 2.550f, 2.875f, 3.200f, 3.525f };
    static const float SpeedTable[] = { 7.22f, 6.13f, 5.36f, 4.63f, 3.64f, 3.00f, 1.90f, 0.38f, 0.39f };
    return Interpolate1D(HeightTable, SpeedTable, 9, HeightAboveGroundM);
}

FVector ASZTUWindFieldActor::LocalMetersToWorld(const FVector& LocalMeters) const
{
    return GetActorTransform().TransformPosition(LocalMeters * 100.0f);
}

void ASZTUWindFieldActor::DrawWindField() const
{
    if (!bShowWindArrows) { return; }
    if (!GetWorld()) { return; }

    const float HalfW = ArrayWidthM * 0.5f;
    const float HalfH = ArrayHeightM * 0.5f;
    const FVector C0 = LocalMetersToWorld(FVector(0.0f, -HalfW, -HalfH));
    const FVector C1 = LocalMetersToWorld(FVector(0.0f, HalfW, -HalfH));
    const FVector C2 = LocalMetersToWorld(FVector(0.0f, HalfW, HalfH));
    const FVector C3 = LocalMetersToWorld(FVector(0.0f, -HalfW, HalfH));

    DrawDebugLine(GetWorld(), C0, C1, FColor::Cyan, false, -1.0f, 0, 3.0f);
    DrawDebugLine(GetWorld(), C1, C2, FColor::Cyan, false, -1.0f, 0, 3.0f);
    DrawDebugLine(GetWorld(), C2, C3, FColor::Cyan, false, -1.0f, 0, 3.0f);
    DrawDebugLine(GetWorld(), C3, C0, FColor::Cyan, false, -1.0f, 0, 3.0f);

    const int32 Step = FMath::Max(1, VisualizationStride);
    for (int32 Row = 0; Row < FanRows; Row += Step)
    {
        for (int32 Col = 0; Col < FanColumns; Col += Step)
        {
            const float Y = FMath::Lerp(-HalfW, HalfW, FanColumns <= 1 ? 0.0f : float(Col) / float(FanColumns - 1));
            const float Z = FMath::Lerp(-HalfH, HalfH, FanRows <= 1 ? 0.0f : float(Row) / float(FanRows - 1));
            const FVector Start = LocalMetersToWorld(FVector(0.0f, Y, Z));
            const FSZTUWindSample Sample = SampleWindAtWorldLocation(Start);
            // 可视化“风吹来的方向”（来向 = -风速方向）：箭头从采样点指向上游。
            // 物理风速 Velocity 仍表示“风往哪去(+X)”，此处仅反转显示方向，
            // 以与场景里手动放置的参考箭头(指向 -X)视觉一致；不影响旗子/无人机受力。
            const FVector End = Start - Sample.VelocityMps * ArrowScaleCmPerMps;
            const float Alpha = FMath::Clamp(Sample.SpeedMps / 22.0f, 0.0f, 1.0f);
            const FColor Color = FLinearColor::LerpUsingHSV(FLinearColor::Green, FLinearColor::Red, Alpha).ToFColor(true);
            DrawDebugLine(GetWorld(), Start, End, Color, false, -1.0f, 0, 1.5f);
        }
    }
}
