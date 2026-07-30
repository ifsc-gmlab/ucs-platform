#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ProceduralMeshComponent.h"
#include "SZTUWindFlag.generated.h"

class ASZTUWindFieldActor;
class UStaticMeshComponent;

UCLASS(Blueprintable)
class SZTUWINDFIELDPX4DEMO_API ASZTUWindFlag : public AActor
{
    GENERATED_BODY()

public:
    ASZTUWindFlag();

    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void OnConstruction(const FTransform& Transform) override;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    USceneComponent* SceneRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UStaticMeshComponent* Pole;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Components")
    UProceduralMeshComponent* FlagMesh;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float FlagWidth = 160.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float FlagHeight = 100.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    int32 GridCols = 24;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    int32 GridRows = 14;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float WindDragCoeff = 8.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float SpringStiffness = 2.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float Damping = 2.4f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float MaxDisplacement = 200.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag", meta = (ClampMin = "0.1"))
    float Mass = 0.4f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float FlutterAmplitude = 18.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float GravityScale = 0.18f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "1", ClampMax = "12"))
    int32 ConstraintIterations = 5;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float StretchConstraintStrength = 0.78f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float ShearConstraintStrength = 0.28f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0", ClampMax = "1.0"))
    float BendConstraintStrength = 0.06f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0"))
    float InitialSagCm = 35.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag Cloth", meta = (ClampMin = "0.0"))
    float InitialFoldCm = 2.5f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    float PoleHeight = 220.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Flag")
    FColor FlagColor = FColor(200, 30, 30);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Debug")
    bool bDebugDrawTip = false;

    // 风场引用：编辑器中直接指派（真正的“独立”组合方式）；
    // 留空则运行时全局查找场景里已摆放的风场兜底。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Wind Field")
    ASZTUWindFieldActor* WindFieldReference = nullptr;

private:
    UPROPERTY()
    UMaterialInterface* CachedDefaultMat;

private:
    void ApplyFlagMaterials();
    void BuildFlagGeometry();
    void UpdateFlagSimulation(float DeltaSeconds);
    void UpdateSoftFlagSimulation(float DeltaSeconds);
    void UpdateSurfaceNormals();
    FVector GetVertexWindForce(const FVector& WorldPos) const;

    TArray<FVector> RestPositions;
    TArray<FVector> Positions;
    TArray<FVector> Velocities;
    TArray<FVector> SurfaceNormals;
    TArray<FProcMeshTangent> SurfaceTangents;
    TArray<int32> FixedVertices;

    UPROPERTY()
    ASZTUWindFieldActor* CachedWindField = nullptr;
};
