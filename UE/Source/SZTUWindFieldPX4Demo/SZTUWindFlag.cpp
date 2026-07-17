#include "SZTUWindFlag.h"
#include "SZTUWindFieldActor.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "UObject/ConstructorHelpers.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "DrawDebugHelpers.h"

ASZTUWindFlag::ASZTUWindFlag()
{
    PrimaryActorTick.bCanEverTick = true;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
    RootComponent = SceneRoot;

    // 旗杆（细长圆柱，看起来像真实旗杆）
    Pole = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Pole"));
    Pole->SetupAttachment(SceneRoot);
    static ConstructorHelpers::FObjectFinder<UStaticMesh> CylMesh(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    if (CylMesh.Succeeded())
    {
        Pole->SetStaticMesh(CylMesh.Object);
        Pole->SetRelativeScale3D(FVector(0.035f, 0.035f, 2.2f));
        Pole->SetRelativeLocation(FVector(0.0f, 0.0f, 110.0f));
    }

    // 旗面（移到旗杆顶部）
    FlagMesh = CreateDefaultSubobject<UProceduralMeshComponent>(TEXT("FlagMesh"));
    FlagMesh->SetupAttachment(SceneRoot);
    FlagMesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    FlagMesh->SetCastShadow(false);
    FlagMesh->SetRelativeLocation(FVector(0.0f, 0.0f, 180.0f));
    // 旗面：M_FlagColor（红色）
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> FlagMat(TEXT("/Game/Drone/Materials/M_FlagColor.M_FlagColor"));
    if (FlagMat.Succeeded())
    {
        FlagMesh->SetMaterial(0, FlagMat.Object);
        UE_LOG(LogTemp, Display, TEXT("Flag material: M_FlagColor (red)"));
    }

    // 旗杆：M_PoleColor（深棕）
    static ConstructorHelpers::FObjectFinder<UMaterialInterface> PoleMat(TEXT("/Game/Drone/Materials/M_PoleColor.M_PoleColor"));
    if (PoleMat.Succeeded() && CylMesh.Succeeded())
    {
        Pole->SetMaterial(0, PoleMat.Object);
        UE_LOG(LogTemp, Display, TEXT("Pole material: M_PoleColor (brown)"));
    }
}

void ASZTUWindFlag::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    BuildFlagGeometry();
}

void ASZTUWindFlag::BeginPlay()
{
    Super::BeginPlay();
    if (RestPositions.Num() == 0) { BuildFlagGeometry(); }
    ApplyFlagMaterials();
    // 材质已在构造函数中设好，BeginPlay 不再重复设置
}

void ASZTUWindFlag::ApplyFlagMaterials()
{
    UMaterialInterface* FlagMat = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/Drone/Materials/M_FlagColor.M_FlagColor"));
    if (FlagMesh && FlagMat)
    {
        UMaterialInstanceDynamic* FlagDMID = UMaterialInstanceDynamic::Create(FlagMat, this);
        if (FlagDMID)
        {
            FlagDMID->SetVectorParameterValue(TEXT("BaseColor"), FLinearColor(0.50f, 0.025f, 0.015f, 1.0f));
            FlagDMID->SetScalarParameterValue(TEXT("Roughness"), 0.98f);
            FlagDMID->SetScalarParameterValue(TEXT("Specular"), 0.0f);
            FlagMesh->SetMaterial(0, FlagDMID);
        }
        else
        {
            FlagMesh->SetMaterial(0, FlagMat);
        }
        UE_LOG(LogTemp, Display, TEXT("Flag runtime material applied: M_FlagColor matte red"));
    }
}

void ASZTUWindFlag::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);

    // 优先使用编辑器中显式指派的引用，否则全局查找场景里已摆放的风场
    if (WindFieldReference)
    {
        CachedWindField = WindFieldReference;
    }
    else if (!CachedWindField)
    {
        for (TActorIterator<ASZTUWindFieldActor> It(GetWorld()); It; ++It)
        {
            CachedWindField = *It;
            break;
        }
    }

    UpdateFlagSimulation(DeltaSeconds);

    // Debug: 在旗尖画圆球，确认物理是否产生位移
    if (bDebugDrawTip && Positions.Num() > 0 && GetWorld())
    {
        const int32 TipIdx = Positions.Num() - 1;
        const FVector TipWorld = GetActorTransform().TransformPosition(Positions[TipIdx]);
        const FVector RestWorld = GetActorTransform().TransformPosition(RestPositions[TipIdx]);

        // 当前位置（红色球）
        DrawDebugSphere(GetWorld(), TipWorld, 4.0f, 8, FColor::Red, false, 0.0f, 0, 2.0f);
        // 原始位置（绿色球）
        DrawDebugSphere(GetWorld(), RestWorld, 4.0f, 8, FColor::Green, false, 0.0f, 0, 2.0f);
        // 连线
        DrawDebugLine(GetWorld(), RestWorld, TipWorld, FColor::Yellow, false, 0.0f, 0, 1.5f);

        // 每 30 帧输出一次位移量
        static int32 FrameCount = 0;
        if (++FrameCount % 30 == 0)
        {
            const float DispX = Positions[TipIdx].X - RestPositions[TipIdx].X;
            UE_LOG(LogTemp, Display, TEXT("Flag tip X displacement: %.1f cm (vel=%.1f)"),
                DispX, Velocities[TipIdx].X);
        }
    }
}

void ASZTUWindFlag::BuildFlagGeometry()
{
    const int32 NumCols = GridCols + 1;
    const int32 NumRows = GridRows + 1;
    const float CellW = FlagWidth / GridCols;
    const float CellH = FlagHeight / GridRows;

    TArray<FVector> Vertices;
    TArray<int32> Triangles;
    TArray<FVector2D> UVs;
    TArray<FVector> Normals;
    TArray<FColor> Colors;
    TArray<FProcMeshTangent> Tangents;

    RestPositions.Reset();
    FixedVertices.Reset();

    // ★ 几何体重定向：旗面沿 Y 轴展开（宽度），法线指向 X 轴（风向）
    // 之前是 X=宽度,Y=法线 → 现在是 Y=宽度,X=法线
    for (int32 Row = 0; Row < NumRows; ++Row)
    {
        for (int32 Col = 0; Col < NumCols; ++Col)
        {
            FVector Pos(0.0f, Col * CellW, Row * CellH);
            Vertices.Add(Pos);
            RestPositions.Add(Pos);
            UVs.Add(FVector2D((float)Col / GridCols, (float)Row / GridRows));
            Colors.Add(FColor(200, 30, 30));
            if (Col == 0) { FixedVertices.Add(Row * NumCols + Col); }
        }
    }

    // 正面 + 反面三角（双面几何）
    for (int32 Row = 0; Row < GridRows; ++Row)
    {
        for (int32 Col = 0; Col < GridCols; ++Col)
        {
            int32 A = Row * NumCols + Col;
            int32 B = A + 1;
            int32 C = A + NumCols;
            int32 D = C + 1;
            // 正面（法线朝 X）
            Triangles.Add(A); Triangles.Add(B); Triangles.Add(D);
            Triangles.Add(A); Triangles.Add(D); Triangles.Add(C);
            // 反面
            Triangles.Add(A); Triangles.Add(D); Triangles.Add(B);
            Triangles.Add(A); Triangles.Add(C); Triangles.Add(D);
        }
    }

    Normals.Init(FVector(1.0f, 0.0f, 0.0f), Vertices.Num());
    Tangents.Init(FProcMeshTangent(0.0f, 1.0f, 0.0f), Vertices.Num());

    FlagMesh->ClearAllMeshSections();
    FlagMesh->CreateMeshSection(0, Vertices, Triangles, Normals, UVs, Colors, Tangents, false);
    ApplyFlagMaterials();

    Positions = RestPositions;
    Velocities.SetNumZeroed(Positions.Num());

    UE_LOG(LogTemp, Display, TEXT("Flag built: %dx%d grid, %d verts, %d tris"),
        GridCols, GridRows, Vertices.Num(), Triangles.Num());
}

void ASZTUWindFlag::UpdateFlagSimulation(float DeltaSeconds)
{
    if (RestPositions.Num() == 0) return;

    // [debug] 首次运行时打印状态
    static bool bDebugged = false;
    if (!bDebugged && GetWorld())
    {
        bDebugged = true;
        UE_LOG(LogTemp, Display, TEXT("Flag tick: CachedWindField=%s, World=%s, Time=%.2f"),
            CachedWindField ? TEXT("found") : TEXT("null"),
            *GetWorld()->GetName(),
            GetWorld()->GetTimeSeconds());
    }

    // 子步积分：固定步长 1/120s（比之前 1/60 更稳定）
    constexpr float FixedSubStep = 1.0f / 120.0f;
    const int32 NumSubSteps = FMath::Clamp(FMath::CeilToInt(DeltaSeconds / FixedSubStep), 1, 8);
    const float DT = DeltaSeconds / float(NumSubSteps);
    const float InvMass = 1.0f / FMath::Max(Mass, 0.1f);

    for (int32 Step = 0; Step < NumSubSteps; ++Step)
    {
        for (int32 i = 0; i < Positions.Num(); ++i)
        {
            if (FixedVertices.Contains(i))
            {
                Positions[i] = RestPositions[i];
                Velocities[i] = FVector::ZeroVector;
                continue;
            }

            const FVector WorldPos = GetActorTransform().TransformPosition(Positions[i]);

            // 风力（风场采样 × 阻力系数）
            const FVector WindForce = GetVertexWindForce(WorldPos);

            // 弹簧恢复力（Hooke's law: F = -k * Δx）
            const FVector SpringForce = (RestPositions[i] - Positions[i]) * SpringStiffness;

            // 阻尼力（F = -c * v）
            const FVector DampingForce = -Velocities[i] * Damping;

            // 旗帜飘动抖振（两个不同频率的噪声叠加）
            const float Flutter = FlutterAmplitude * (
                FMath::Sin(WorldPos.X * 0.15f + WorldPos.Z * 0.20f + GetWorld()->GetTimeSeconds() * 12.0f) * 0.43f +
                FMath::Sin(WorldPos.X * 0.08f + WorldPos.Z * 0.12f + GetWorld()->GetTimeSeconds() *  7.3f + 1.5f) * 0.57f
            );
            // ★ 几何体重定向后，抖振沿 X 轴（法线/风向方向）
            const FVector FlutterForce(Flutter, 0.0f, 0.0f);

            // 距旗杆越远，风/抖振的影响越大（杠杆效应）
            // ★ 重定向后旗面沿 Y 轴展开
            const float DistFromPole = Positions[i].Y / FMath::Max(FlagWidth, 1.0f);
            const float LeverScale = FMath::Pow(DistFromPole, 0.5f);

            // 弹簧和阻尼力不乘 LeverScale（它们是材料属性，与位置无关）
            // 只有风力和抖振受杠杆效应影响
            const FVector Acceleration = (SpringForce + DampingForce) + (WindForce + FlutterForce) * LeverScale;
            Velocities[i] += Acceleration * InvMass * DT;
            Velocities[i] = Velocities[i].GetClampedToMaxSize(MaxDisplacement * 5.0f);
            Positions[i] += Velocities[i] * DT;

            // 位移约束：不超过 MaxDisplacement
            const FVector Displacement = Positions[i] - RestPositions[i];
            const float DispLen = Displacement.Size();
            if (DispLen > MaxDisplacement)
            {
                Positions[i] = RestPositions[i] + Displacement / DispLen * MaxDisplacement;
            }

            // ★ X 轴边界约束：不让旗面穿到旗杆另一侧（重定向后宽度沿 Y）
            Positions[i].X = FMath::Clamp(Positions[i].X, -FlagWidth * 0.6f, FlagWidth * 0.6f);
        }
    }

    // 更新 ProceduralMesh 顶点位置
    FlagMesh->UpdateMeshSection(0, Positions, TArray<FVector>(), TArray<FVector2D>(), TArray<FColor>(), TArray<FProcMeshTangent>());
}

FVector ASZTUWindFlag::GetVertexWindForce(const FVector& WorldPos) const
{
    if (CachedWindField)
    {
        FSZTUWindSample Sample = CachedWindField->SampleWindAtWorldLocation(WorldPos);
        // ★ 几何体重定向后，局部 X 轴 = 旗面法线 = 风向
        // 将世界风速转换到局部空间，取 X 分量
        const FVector LocalWind = GetActorTransform().InverseTransformVectorNoScale(Sample.VelocityMps);
        return FVector(LocalWind.X * WindDragCoeff, 0.0f, 0.0f);
    }
    // 没找到风场时用常风兜底
    return FVector(10.0f * WindDragCoeff, 0.0f, 0.0f);
}
