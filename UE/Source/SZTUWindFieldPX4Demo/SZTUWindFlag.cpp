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
    FlagMesh->SetCastShadow(true);
    FlagMesh->SetRelativeLocation(FVector(0.0f, 0.0f, 150.0f));
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
            const FLinearColor FabricRed = FLinearColor::FromSRGBColor(FColor(150, 31, 32));
            FlagDMID->SetVectorParameterValue(TEXT("BaseColor"), FabricRed);
            FlagDMID->SetScalarParameterValue(TEXT("Roughness"), 0.85f);
            FlagDMID->SetScalarParameterValue(TEXT("Specular"), 0.12f);
            FlagDMID->SetScalarParameterValue(TEXT("AmbientFill"), 0.08f);
            FlagMesh->SetMaterial(0, FlagDMID);
        }
        else
        {
            FlagMesh->SetMaterial(0, FlagMat);
        }
        UE_LOG(LogTemp, Display, TEXT("Flag runtime material applied: M_FlagColor lit fabric red"));
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

    UpdateSoftFlagSimulation(DeltaSeconds);

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

    // One geometric surface is enough; M_FlagColor handles two-sided rendering.
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
        }
    }

    Normals.Init(FVector(1.0f, 0.0f, 0.0f), Vertices.Num());
    Tangents.Init(FProcMeshTangent(0.0f, 1.0f, 0.0f), Vertices.Num());

    FlagMesh->ClearAllMeshSections();
    FlagMesh->CreateMeshSection(0, Vertices, Triangles, Normals, UVs, Colors, Tangents, false);
    ApplyFlagMaterials();

    Positions = RestPositions;
    for (int32 Row = 0; Row < NumRows; ++Row)
    {
        for (int32 Col = 1; Col < NumCols; ++Col)
        {
            const int32 Index = Row * NumCols + Col;
            const float AlongFlag = float(Col) / float(GridCols);
            Positions[Index].Z -= InitialSagCm * FMath::Pow(AlongFlag, 1.35f);
            Positions[Index].X += InitialFoldCm
                * FMath::Sin(float(Row) * 0.9f + float(Col) * 0.45f)
                * AlongFlag;
        }
    }
    Velocities.SetNumZeroed(Positions.Num());
    UpdateSurfaceNormals();

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

void ASZTUWindFlag::UpdateSoftFlagSimulation(float DeltaSeconds)
{
    if (RestPositions.Num() == 0 || Positions.Num() != RestPositions.Num()) { return; }

    constexpr float FixedSubStep = 1.0f / 120.0f;
    const int32 NumSubSteps = FMath::Clamp(FMath::CeilToInt(DeltaSeconds / FixedSubStep), 1, 8);
    const float DT = DeltaSeconds / float(NumSubSteps);
    const float InvMass = 1.0f / FMath::Max(Mass, 0.1f);
    const int32 NumCols = GridCols + 1;
    const int32 NumRows = GridRows + 1;
    const float CellW = FlagWidth / FMath::Max(GridCols, 1);
    const float CellH = FlagHeight / FMath::Max(GridRows, 1);
    const float DiagonalLength = FMath::Sqrt(CellW * CellW + CellH * CellH);
    const FVector LocalGravity = GetActorTransform().InverseTransformVectorNoScale(
        FVector(0.0f, 0.0f, -980.0f * GravityScale));

    const auto IsFixed = [NumCols](int32 Index)
    {
        return Index % NumCols == 0;
    };

    for (int32 Step = 0; Step < NumSubSteps; ++Step)
    {
        const TArray<FVector> PreviousPositions = Positions;

        for (int32 Index = 0; Index < Positions.Num(); ++Index)
        {
            if (IsFixed(Index))
            {
                Positions[Index] = RestPositions[Index];
                Velocities[Index] = FVector::ZeroVector;
                continue;
            }

            const FVector WorldPos = GetActorTransform().TransformPosition(Positions[Index]);
            const FVector WindForce = GetVertexWindForce(WorldPos);
            const float WindSpeedMps = FMath::Abs(WindDragCoeff) > KINDA_SMALL_NUMBER
                ? FMath::Abs(WindForce.X / WindDragCoeff)
                : 0.0f;
            const float FlutterScale = FMath::Clamp(WindSpeedMps / 8.0f, 0.0f, 1.5f);
            const float AlongFlag = FMath::Clamp(RestPositions[Index].Y / FMath::Max(FlagWidth, 1.0f), 0.0f, 1.0f);
            const float HeightPhase = RestPositions[Index].Z / FMath::Max(FlagHeight, 1.0f);
            const float Time = GetWorld() ? GetWorld()->GetTimeSeconds() : 0.0f;
            const float Flutter = FlutterAmplitude * FlutterScale * (
                FMath::Sin(AlongFlag * 11.0f - Time * 8.0f + HeightPhase * 2.5f) * 0.65f
                + FMath::Sin(AlongFlag * 19.0f - Time * 12.5f - HeightPhase * 4.0f) * 0.35f);
            const FVector FlutterForce(Flutter, 0.0f, Flutter * 0.08f);
            const float LeverScale = FMath::Sqrt(AlongFlag);
            const FVector Acceleration = LocalGravity + (WindForce + FlutterForce) * LeverScale * InvMass;

            Velocities[Index] += Acceleration * DT;
            Velocities[Index] *= FMath::Exp(-Damping * DT);
            Velocities[Index] = Velocities[Index].GetClampedToMaxSize(MaxDisplacement * 5.0f);
            Positions[Index] += Velocities[Index] * DT;
        }

        const auto SolveDistanceConstraint = [&](int32 A, int32 B, float RestLength, float Strength)
        {
            FVector Delta = Positions[B] - Positions[A];
            const float Length = Delta.Size();
            if (Length <= KINDA_SMALL_NUMBER) { return; }

            const FVector Correction = Delta * ((Length - RestLength) / Length) * Strength;
            const bool bFixedA = IsFixed(A);
            const bool bFixedB = IsFixed(B);
            if (bFixedA && !bFixedB)
            {
                Positions[B] -= Correction;
            }
            else if (!bFixedA && bFixedB)
            {
                Positions[A] += Correction;
            }
            else if (!bFixedA && !bFixedB)
            {
                Positions[A] += Correction * 0.5f;
                Positions[B] -= Correction * 0.5f;
            }
        };

        for (int32 Iteration = 0; Iteration < FMath::Max(ConstraintIterations, 1); ++Iteration)
        {
            for (int32 Row = 0; Row < NumRows; ++Row)
            {
                for (int32 Col = 0; Col < NumCols; ++Col)
                {
                    const int32 A = Row * NumCols + Col;
                    if (Col < GridCols)
                    {
                        SolveDistanceConstraint(A, A + 1, CellW, StretchConstraintStrength);
                    }
                    if (Row < GridRows)
                    {
                        SolveDistanceConstraint(A, A + NumCols, CellH, StretchConstraintStrength);
                    }
                    if (Col < GridCols && Row < GridRows)
                    {
                        SolveDistanceConstraint(A, A + NumCols + 1, DiagonalLength, ShearConstraintStrength);
                        SolveDistanceConstraint(A + 1, A + NumCols, DiagonalLength, ShearConstraintStrength);
                    }
                    if (Col + 2 < NumCols)
                    {
                        SolveDistanceConstraint(A, A + 2, CellW * 2.0f, BendConstraintStrength);
                    }
                    if (Row + 2 < NumRows)
                    {
                        SolveDistanceConstraint(A, A + NumCols * 2, CellH * 2.0f, BendConstraintStrength);
                    }
                }
            }

            for (int32 Row = 0; Row < NumRows; ++Row)
            {
                const int32 FixedIndex = Row * NumCols;
                Positions[FixedIndex] = RestPositions[FixedIndex];
            }
        }

        for (int32 Index = 0; Index < Positions.Num(); ++Index)
        {
            if (IsFixed(Index))
            {
                Positions[Index] = RestPositions[Index];
                Velocities[Index] = FVector::ZeroVector;
                continue;
            }

            const FVector Displacement = Positions[Index] - RestPositions[Index];
            if (Displacement.SizeSquared() > FMath::Square(MaxDisplacement))
            {
                Positions[Index] = RestPositions[Index] + Displacement.GetSafeNormal() * MaxDisplacement;
            }
            Positions[Index].X = FMath::Clamp(Positions[Index].X, -FlagWidth * 0.6f, FlagWidth * 0.6f);
            Velocities[Index] = (Positions[Index] - PreviousPositions[Index]) / FMath::Max(DT, KINDA_SMALL_NUMBER);
        }
    }

    UpdateSurfaceNormals();
    FlagMesh->UpdateMeshSection(
        0, Positions, SurfaceNormals, TArray<FVector2D>(), TArray<FColor>(), SurfaceTangents);
}

void ASZTUWindFlag::UpdateSurfaceNormals()
{
    const int32 NumCols = GridCols + 1;
    const int32 NumRows = GridRows + 1;
    if (Positions.Num() != NumCols * NumRows) { return; }

    SurfaceNormals.Init(FVector::ZeroVector, Positions.Num());
    for (int32 Row = 0; Row < GridRows; ++Row)
    {
        for (int32 Col = 0; Col < GridCols; ++Col)
        {
            const int32 A = Row * NumCols + Col;
            const int32 B = A + 1;
            const int32 C = A + NumCols;
            const int32 D = C + 1;
            const FVector N0 = FVector::CrossProduct(Positions[B] - Positions[A], Positions[D] - Positions[A]);
            const FVector N1 = FVector::CrossProduct(Positions[D] - Positions[A], Positions[C] - Positions[A]);
            SurfaceNormals[A] += N0 + N1;
            SurfaceNormals[B] += N0;
            SurfaceNormals[C] += N1;
            SurfaceNormals[D] += N0 + N1;
        }
    }

    SurfaceTangents.SetNum(Positions.Num());
    for (int32 Row = 0; Row < NumRows; ++Row)
    {
        for (int32 Col = 0; Col < NumCols; ++Col)
        {
            const int32 Index = Row * NumCols + Col;
            SurfaceNormals[Index] = SurfaceNormals[Index].GetSafeNormal();
            if (SurfaceNormals[Index].IsNearlyZero())
            {
                SurfaceNormals[Index] = FVector(1.0f, 0.0f, 0.0f);
            }

            const int32 Neighbor = Col < GridCols ? Index + 1 : Index - 1;
            FVector Tangent = (Positions[Neighbor] - Positions[Index]).GetSafeNormal();
            Tangent -= SurfaceNormals[Index] * FVector::DotProduct(Tangent, SurfaceNormals[Index]);
            Tangent = Tangent.GetSafeNormal();
            if (Tangent.IsNearlyZero())
            {
                Tangent = FVector(0.0f, 1.0f, 0.0f);
            }
            SurfaceTangents[Index] = FProcMeshTangent(Tangent.X, Tangent.Y, Tangent.Z);
        }
    }
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
