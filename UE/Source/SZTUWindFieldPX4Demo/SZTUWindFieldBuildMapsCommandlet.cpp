#include "SZTUWindFieldBuildMapsCommandlet.h"

#include "Engine/World.h"
#include "Engine/Level.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "Misc/Paths.h"
#include "Misc/Parse.h"
#include "HAL/FileManager.h"
#include "GameFramework/PlayerStart.h"
#include "GameFramework/WorldSettings.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PointLight.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/TextRenderActor.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/TextRenderComponent.h"
#include "Components/LightComponent.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionMultiply.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "EngineUtils.h"
#include "UObject/UObjectIterator.h"

#include "SZTUWindFieldActor.h"
#include "SZTUWindFlag.h"
#include "SZTUDronePawn.h"
#include "SZTURoverPawn.h"

USZTUWindFieldBuildMapsCommandlet::USZTUWindFieldBuildMapsCommandlet()
{
    IsClient = false;
    IsServer = false;
    IsEditor = true;
    LogToConsole = true;
}

static bool CreateColoredMaterial(
    const FString& MatName,
    const FLinearColor& DefaultColor,
    bool bUnlit = false,
    float Roughness = 0.88f,
    float Specular = 0.08f,
    float AmbientFill = 0.0f)
{
    const FString PackagePath = TEXT("/Game/Drone/Materials/") + MatName;
    FString OldFile = FPaths::ProjectContentDir() / TEXT("Drone/Materials") / MatName + TEXT(".uasset");
    if (IFileManager::Get().FileExists(*OldFile))
    {
        IFileManager::Get().Delete(*OldFile);
    }

    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreatePackage failed: %s"), *PackagePath);
        return false;
    }
    Package->SetFlags(RF_Public | RF_Standalone);

    UMaterial* M = NewObject<UMaterial>(Package, FName(*MatName), RF_Public | RF_Standalone);
    if (!M)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] NewObject UMaterial failed: %s"), *MatName);
        return false;
    }
    M->SetShadingModel(bUnlit ? EMaterialShadingModel::MSM_Unlit : EMaterialShadingModel::MSM_DefaultLit);
    M->BlendMode = EBlendMode::BLEND_Opaque;
    M->TwoSided = true;

    bool bNeedsRecompile = false;
    M->SetMaterialUsage(bNeedsRecompile, MATUSAGE_InstancedStaticMeshes);

    UMaterialExpressionVectorParameter* ColorParam = NewObject<UMaterialExpressionVectorParameter>(M, FName(TEXT("BaseColorParam")));
    UMaterialExpressionScalarParameter* RoughnessParam = NewObject<UMaterialExpressionScalarParameter>(M, FName(TEXT("RoughnessParam")));
    UMaterialExpressionScalarParameter* SpecularParam = NewObject<UMaterialExpressionScalarParameter>(M, FName(TEXT("SpecularParam")));
    UMaterialExpressionScalarParameter* AmbientFillParam = AmbientFill > 0.0f
        ? NewObject<UMaterialExpressionScalarParameter>(M, FName(TEXT("AmbientFillParam")))
        : nullptr;
    UMaterialExpressionMultiply* AmbientFillMultiply = AmbientFillParam
        ? NewObject<UMaterialExpressionMultiply>(M, FName(TEXT("AmbientFillMultiply")))
        : nullptr;
    if (ColorParam)
    {
        ColorParam->ParameterName = TEXT("BaseColor");
        ColorParam->DefaultValue = DefaultColor;
        ColorParam->Material = M;
        M->GetExpressionCollection().AddExpression(ColorParam);
#if WITH_EDITORONLY_DATA
        UMaterialEditorOnlyData* EOD = M->GetEditorOnlyData();
        if (EOD)
        {
            EOD->BaseColor.Expression = ColorParam;
            if (bUnlit)
            {
                EOD->EmissiveColor.Expression = ColorParam;
            }
        }
#endif
    }
    if (RoughnessParam)
    {
        RoughnessParam->ParameterName = TEXT("Roughness");
        RoughnessParam->DefaultValue = Roughness;
        RoughnessParam->Material = M;
        M->GetExpressionCollection().AddExpression(RoughnessParam);
#if WITH_EDITORONLY_DATA
        if (UMaterialEditorOnlyData* EOD = M->GetEditorOnlyData())
        {
            EOD->Roughness.Expression = RoughnessParam;
        }
#endif
    }
    if (SpecularParam)
    {
        SpecularParam->ParameterName = TEXT("Specular");
        SpecularParam->DefaultValue = Specular;
        SpecularParam->Material = M;
        M->GetExpressionCollection().AddExpression(SpecularParam);
#if WITH_EDITORONLY_DATA
        if (UMaterialEditorOnlyData* EOD = M->GetEditorOnlyData())
        {
            EOD->Specular.Expression = SpecularParam;
        }
#endif
    }
    if (AmbientFillParam && AmbientFillMultiply && ColorParam)
    {
        AmbientFillParam->ParameterName = TEXT("AmbientFill");
        AmbientFillParam->DefaultValue = AmbientFill;
        AmbientFillParam->Material = M;
        M->GetExpressionCollection().AddExpression(AmbientFillParam);

        AmbientFillMultiply->A.Expression = ColorParam;
        AmbientFillMultiply->B.Expression = AmbientFillParam;
        AmbientFillMultiply->Material = M;
        M->GetExpressionCollection().AddExpression(AmbientFillMultiply);
#if WITH_EDITORONLY_DATA
        if (UMaterialEditorOnlyData* EOD = M->GetEditorOnlyData())
        {
            EOD->EmissiveColor.Expression = AmbientFillMultiply;
        }
#endif
    }

    M->MarkPackageDirty();

    FString Filename = FPaths::ProjectContentDir() / TEXT("Drone/Materials") / MatName + TEXT(".uasset");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, M, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved material %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));
    return bSaved;
}

static UMaterialInterface* LoadDemoMaterial(const TCHAR* MaterialName)
{
    const FString Path = FString::Printf(TEXT("/Game/Drone/Materials/%s.%s"), MaterialName, MaterialName);
    return LoadObject<UMaterialInterface>(nullptr, *Path);
}

static void ConfigureWorldForRuntimeDemo(UWorld* World)
{
    if (!World) { return; }
    if (AWorldSettings* WS = World->GetWorldSettings())
    {
        WS->bForceNoPrecomputedLighting = true;
    }
}

static void ConfigureDirectionalLight(ADirectionalLight* Dir)
{
    if (!Dir) { return; }
    Dir->SetCastShadows(false);
    Dir->SetBrightness(3.0f);
    if (ULightComponent* Light = Dir->GetLightComponent())
    {
        Light->SetMobility(EComponentMobility::Movable);
        Light->SetCastShadows(false);
    }
}

static void ApplyGroundMaterial(AStaticMeshActor* Ground)
{
    if (!Ground || !Ground->GetStaticMeshComponent()) { return; }
    UMaterialInterface* GroundMat = LoadDemoMaterial(TEXT("M_GroundColor"));
    if (GroundMat)
    {
        Ground->GetStaticMeshComponent()->SetMaterial(0, GroundMat);
    }
}

static AStaticMeshActor* SpawnRepairedGroundPlane(UWorld* World, const FActorSpawnParameters& SpawnParams, const FVector& Center = FVector(250.f, 0.f, 0.5f), float Scale = 100.f)
{
    if (!World) { return nullptr; }

    AStaticMeshActor* Ground = World->SpawnActor<AStaticMeshActor>(AStaticMeshActor::StaticClass(), Center, FRotator::ZeroRotator, SpawnParams);
    if (!Ground || !Ground->GetStaticMeshComponent()) { return Ground; }

    UStaticMesh* Plane = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane"));
    if (Plane)
    {
        Ground->GetStaticMeshComponent()->SetStaticMesh(Plane);
    }

    Ground->SetActorScale3D(FVector(Scale, Scale, 1.f));
    Ground->GetStaticMeshComponent()->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Ground->GetStaticMeshComponent()->SetCastShadow(false);
    ApplyGroundMaterial(Ground);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Added repaired matte ground plane at %s scale=%.1f"), *Center.ToString(), Scale);
    return Ground;
}

static UStaticMesh* LoadFactoryMesh(const TCHAR* AssetName)
{
    const FString Path = FString::Printf(
        TEXT("/Game/TrainEnvironment/Meshes/%s.%s"), AssetName, AssetName);
    UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *Path);
    if (!Mesh)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Factory mesh not found: %s"), *Path);
    }
    return Mesh;
}

static AStaticMeshActor* SpawnFactoryMesh(
    UWorld* World,
    const FActorSpawnParameters& SpawnParams,
    const TCHAR* AssetName,
    const FVector& GroundLocation,
    const FRotator& Rotation = FRotator::ZeroRotator,
    const FVector& Scale = FVector::OneVector,
    bool bCollision = true)
{
    UStaticMesh* Mesh = LoadFactoryMesh(AssetName);
    if (!World || !Mesh) { return nullptr; }

    const FBoxSphereBounds Bounds = Mesh->GetBounds();
    FVector ActorLocation = GroundLocation;
    ActorLocation.Z -= (Bounds.Origin.Z - Bounds.BoxExtent.Z) * Scale.Z;

    AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(), ActorLocation, Rotation, SpawnParams);
    if (!Actor || !Actor->GetStaticMeshComponent()) { return Actor; }

    UStaticMeshComponent* Component = Actor->GetStaticMeshComponent();
    Component->SetStaticMesh(Mesh);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetCollisionEnabled(bCollision
        ? ECollisionEnabled::QueryAndPhysics
        : ECollisionEnabled::NoCollision);
    if (bCollision)
    {
        Component->SetCollisionObjectType(ECC_WorldStatic);
        Component->SetCollisionResponseToAllChannels(ECR_Block);
    }
    Actor->SetActorScale3D(Scale);
    return Actor;
}

static int32 SpawnFactoryMeshLine(
    UWorld* World,
    const FActorSpawnParameters& SpawnParams,
    const TCHAR* AssetName,
    const FVector& Center,
    float Length,
    bool bAlongX)
{
    UStaticMesh* Mesh = LoadFactoryMesh(AssetName);
    if (!World || !Mesh) { return 0; }

    const FVector MeshSize = Mesh->GetBounds().BoxExtent * 2.0f;
    const bool bMeshLongAxisX = MeshSize.X >= MeshSize.Y;
    const float LongAxisSize = FMath::Max(MeshSize.X, MeshSize.Y);
    const float Spacing = FMath::Max(100.0f, LongAxisSize * 0.98f);
    const int32 Count = FMath::Clamp(FMath::CeilToInt(Length / Spacing), 1, 16);
    const float Yaw = bAlongX
        ? (bMeshLongAxisX ? 0.0f : 90.0f)
        : (bMeshLongAxisX ? 90.0f : 0.0f);

    int32 Spawned = 0;
    for (int32 Index = 0; Index < Count; ++Index)
    {
        const float Offset = (static_cast<float>(Index) - 0.5f * (Count - 1)) * Spacing;
        const FVector Location = Center + (bAlongX
            ? FVector(Offset, 0.0f, 0.0f)
            : FVector(0.0f, Offset, 0.0f));
        if (SpawnFactoryMesh(
            World, SpawnParams, AssetName, Location, FRotator(0.0f, Yaw, 0.0f)))
        {
            ++Spawned;
        }
    }
    return Spawned;
}

static AStaticMeshActor* SpawnFactoryTestPad(
    UWorld* World,
    const FActorSpawnParameters& SpawnParams)
{
    UStaticMesh* Cube = LoadObject<UStaticMesh>(
        nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    if (!World || !Cube) { return nullptr; }

    AStaticMeshActor* Ground = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(), FVector(0.0f, 0.0f, -15.0f),
        FRotator::ZeroRotator, SpawnParams);
    if (!Ground || !Ground->GetStaticMeshComponent()) { return Ground; }

    UStaticMeshComponent* Component = Ground->GetStaticMeshComponent();
    Component->SetStaticMesh(Cube);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
    Component->SetCollisionObjectType(ECC_WorldStatic);
    Component->SetCollisionResponseToAllChannels(ECR_Block);
    Component->SetCastShadow(false);
    Ground->SetActorScale3D(FVector(30.0f, 24.0f, 0.3f));

    UMaterialInterface* Asphalt = LoadObject<UMaterialInterface>(
        nullptr,
        TEXT("/Game/TrainEnvironment/Materials/Mat_Asphalt_1.Mat_Asphalt_1"));
    if (Asphalt)
    {
        Component->SetMaterial(0, Asphalt);
    }
    else
    {
        ApplyGroundMaterial(Ground);
        UE_LOG(LogTemp, Warning, TEXT("[BuildMaps] Factory asphalt material missing; using project ground material."));
    }
    return Ground;
}

static AStaticMeshActor* SpawnUrbanBox(
    UWorld* World,
    const FActorSpawnParameters& SpawnParams,
    const FVector& BottomCenterCm,
    const FVector& SizeM,
    const TCHAR* MaterialName,
    bool bCollision = true,
    const TCHAR* SemanticId = nullptr)
{
    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    if (!World || !Cube) { return nullptr; }

    const FVector Location = BottomCenterCm + FVector(0.0f, 0.0f, SizeM.Z * 50.0f);
    AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(
        AStaticMeshActor::StaticClass(), Location, FRotator::ZeroRotator, SpawnParams);
    if (!Actor || !Actor->GetStaticMeshComponent()) { return Actor; }

    UStaticMeshComponent* Component = Actor->GetStaticMeshComponent();
    Component->SetStaticMesh(Cube);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetCollisionEnabled(bCollision
        ? ECollisionEnabled::QueryAndPhysics
        : ECollisionEnabled::NoCollision);
    if (bCollision)
    {
        Component->SetCollisionObjectType(ECC_WorldStatic);
        Component->SetCollisionResponseToAllChannels(ECR_Block);
    }
    Actor->SetActorScale3D(SizeM);
    if (UMaterialInterface* Material = LoadDemoMaterial(MaterialName))
    {
        Component->SetMaterial(0, Material);
    }
    if (SemanticId && FCString::Strlen(SemanticId) > 0)
    {
        Actor->Tags.AddUnique(TEXT("SZTU.SemanticAnchor"));
        Actor->Tags.AddUnique(FName(*FString::Printf(TEXT("semantic:%s"), SemanticId)));
    }
    return Actor;
}

static bool SpawnSemanticAnchor(
    UWorld* World,
    const FActorSpawnParameters& SpawnParams,
    const FVector& LocationCm,
    const TCHAR* SemanticId,
    const TCHAR* MaterialName)
{
    // A true hollow ground logo: four thin, collision-free strips. The former
    // outer-plane + inner-plane stack still looked filled under colored point
    // lights and produced z-fighting. Nothing now occupies the logo interior.
    if (!World) { return false; }
    constexpr float SideM = 3.0f;
    constexpr float StrokeM = 0.22f;
    constexpr float HalfOffsetCm = (SideM - StrokeM) * 50.0f;
    const FVector Base = LocationCm + FVector(0.0f, 0.0f, 2.5f);
    TArray<AStaticMeshActor*> Strips;
    Strips.Add(SpawnUrbanBox(
        World, SpawnParams, Base + FVector(0.0f, HalfOffsetCm, 0.0f),
        FVector(SideM, StrokeM, 0.02f), MaterialName, false));
    Strips.Add(SpawnUrbanBox(
        World, SpawnParams, Base - FVector(0.0f, HalfOffsetCm, 0.0f),
        FVector(SideM, StrokeM, 0.02f), MaterialName, false));
    Strips.Add(SpawnUrbanBox(
        World, SpawnParams, Base + FVector(HalfOffsetCm, 0.0f, 0.0f),
        FVector(StrokeM, SideM, 0.02f), MaterialName, false));
    Strips.Add(SpawnUrbanBox(
        World, SpawnParams, Base - FVector(HalfOffsetCm, 0.0f, 0.0f),
        FVector(StrokeM, SideM, 0.02f), MaterialName, false));
    for (AStaticMeshActor* Strip : Strips)
    {
        if (!Strip) { return false; }
        Strip->Tags.AddUnique(TEXT("SZTU.SemanticAnchor"));
        Strip->Tags.AddUnique(FName(*FString::Printf(
            TEXT("semantic:%s"), SemanticId)));
    }
    return true;
}

static bool BuildUrbanSemanticMap(const FString& MapName)
{
    const FString PackagePath = TEXT("/Game/Maps/") + MapName;
    const FString OldFile = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    if (IFileManager::Get().FileExists(*OldFile))
    {
        IFileManager::Get().Delete(*OldFile);
    }

    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package) { return false; }
    Package->SetFlags(RF_Public | RF_Standalone);

    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*MapName), Package);
    if (!World) { return false; }
    World->SetFlags(RF_Public | RF_Standalone);
    ConfigureWorldForRuntimeDemo(World);

    FActorSpawnParameters SP;
    SP.bNoFail = true;
    SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

    // Drone world origin is (2.5, 0, 0)m. The semantic-map JSON uses this as
    // local-NED (0, 0, 0), so visual labels and controller coordinates agree.
    ASZTUDronePawn* Drone = World->SpawnActor<ASZTUDronePawn>(
        ASZTUDronePawn::StaticClass(), FVector(250.0f, 0.0f, 0.0f),
        FRotator::ZeroRotator, SP);
    if (Drone)
    {
        Drone->Airframe = ESZTUMultirotorAirframe::QuadX;
        Drone->DroneId = 1;
        Drone->bAutoSpawnWindField = false;
        Drone->bEnableProximitySensors = true;
        Drone->bEnableHorizontalCollisionSweep = true;
        Drone->ProximityMaxRangeM = 12.0f;
        Drone->ProximityUpdateRateHz = 10.0f;
        Drone->AutoPossessPlayer = EAutoReceiveInput::Player0;
    }

    World->SpawnActor<APlayerStart>(
        APlayerStart::StaticClass(), FVector(250.0f, 0.0f, 0.0f),
        FRotator::ZeroRotator, SP);

    const bool bGroundOk = SpawnUrbanBox(
        World, SP, FVector(1200.0f, 0.0f, -30.0f), FVector(42.0f, 26.0f, 0.30f),
        TEXT("M_UrbanGround"), true) != nullptr;
    const bool bRoadOk = SpawnUrbanBox(
        World, SP, FVector(1200.0f, 0.0f, 0.5f), FVector(34.0f, 12.0f, 0.02f),
        TEXT("M_UrbanRoad"), false) != nullptr;

    bool bLaneOk = true;
    for (int32 Index = 0; Index < 9; ++Index)
    {
        bLaneOk &= SpawnUrbanBox(
            World, SP, FVector(250.0f + Index * 250.0f, 0.0f, 3.0f),
            FVector(1.2f, 0.08f, 0.02f), TEXT("M_UrbanLane"), false) != nullptr;
    }

    bool bBuildingsOk = true;
    const float BuildingX[] = { -200.0f, 450.0f, 1100.0f, 1750.0f, 2400.0f };
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(BuildingX); ++Index)
    {
        const float HeightM = 5.0f + static_cast<float>(Index % 3) * 1.8f;
        bBuildingsOk &= SpawnUrbanBox(
            World, SP, FVector(BuildingX[Index], -900.0f, 0.0f),
            FVector(4.6f, 5.0f, HeightM), TEXT("M_UrbanBuilding"), true) != nullptr;
        bBuildingsOk &= SpawnUrbanBox(
            World, SP, FVector(BuildingX[Index] + 180.0f, 900.0f, 0.0f),
            FVector(4.2f, 5.0f, HeightM + 1.0f), TEXT("M_UrbanBuildingAlt"), true) != nullptr;
    }

    // Mandatory obstacle on the A -> B centerline. The 4.6m width requires a
    // visible lateral detour while leaving more than 3m clearance on either side.
    AStaticMeshActor* CentralObstacle = SpawnUrbanBox(
        World, SP, FVector(1050.0f, 0.0f, 0.0f), FVector(2.0f, 4.6f, 5.5f),
        TEXT("M_UrbanObstacle"), true);
    if (CentralObstacle)
    {
        CentralObstacle->Tags.AddUnique(TEXT("SZTU.Obstacle"));
    }

    const bool bAnchorsOk =
        SpawnSemanticAnchor(World, SP, FVector(250.0f, 0.0f, 0.0f),
            TEXT("start_pad"), TEXT("M_SemanticStart")) &&
        SpawnSemanticAnchor(World, SP, FVector(850.0f, -400.0f, 0.0f),
            TEXT("inspection_gate"), TEXT("M_SemanticInspection")) &&
        SpawnSemanticAnchor(World, SP, FVector(2050.0f, 0.0f, 0.0f),
            TEXT("delivery_zone"), TEXT("M_SemanticDelivery")) &&
        SpawnSemanticAnchor(World, SP, FVector(2050.0f, 400.0f, 0.0f),
            TEXT("medical_station"), TEXT("M_SemanticMedical"));

    ASkyAtmosphere* Sky = World->SpawnActor<ASkyAtmosphere>(
        ASkyAtmosphere::StaticClass(), FVector::ZeroVector, FRotator::ZeroRotator, SP);
    ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(
        ADirectionalLight::StaticClass(), FVector(0.0f, 0.0f, 900.0f),
        FRotator(-52.0f, -28.0f, 0.0f), SP);
    if (Sun)
    {
        ConfigureDirectionalLight(Sun);
        Sun->SetBrightness(1.4f);
        if (UDirectionalLightComponent* SunComponent = Cast<UDirectionalLightComponent>(Sun->GetLightComponent()))
        {
            SunComponent->SetAtmosphereSunLight(true);
            SunComponent->SetLightColor(FLinearColor(1.0f, 0.95f, 0.86f));
        }
    }
    APointLight* Fill = World->SpawnActor<APointLight>(
        APointLight::StaticClass(), FVector(1100.0f, -400.0f, 1100.0f),
        FRotator::ZeroRotator, SP);
    if (Fill && Fill->PointLightComponent)
    {
        Fill->PointLightComponent->SetMobility(EComponentMobility::Movable);
        Fill->PointLightComponent->SetCastShadows(false);
        Fill->PointLightComponent->SetIntensity(2600.0f);
        Fill->PointLightComponent->SetAttenuationRadius(3200.0f);
    }

    const bool bLayoutOk = Drone && bGroundOk && bRoadOk && bLaneOk && bBuildingsOk &&
        CentralObstacle && bAnchorsOk && Sky && Sun && Fill;
    UE_LOG(LogTemp, Display,
        TEXT("[BuildMaps] Urban semantic layout: drone=%s buildings=%s obstacle=%s anchors=%s status=%s"),
        Drone ? TEXT("OK") : TEXT("FAIL"),
        bBuildingsOk ? TEXT("OK") : TEXT("FAIL"),
        CentralObstacle ? TEXT("OK") : TEXT("FAIL"),
        bAnchorsOk ? TEXT("OK") : TEXT("FAIL"),
        bLayoutOk ? TEXT("OK") : TEXT("INCOMPLETE"));

    World->MarkPackageDirty();
    const FString Filename = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, World, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));
    World->CleanupWorld();
    return bLayoutOk && bSaved;
}

static bool IsGroundLikeStaticMeshActor(AStaticMeshActor* Actor)
{
    if (!Actor || !Actor->GetStaticMeshComponent()) { return false; }

    const FString Name = Actor->GetName().ToLower();
    UStaticMesh* Mesh = Actor->GetStaticMeshComponent()->GetStaticMesh();
    const FString MeshPath = Mesh ? Mesh->GetPathName().ToLower() : FString();

    return Name.Contains(TEXT("ground")) ||
           Name.Contains(TEXT("floor")) ||
           Name.Contains(TEXT("plane")) ||
           MeshPath.Contains(TEXT("/engine/basicshapes/plane")) ||
           MeshPath.Contains(TEXT("template_map_floor"));
}

static int32 RemoveGroundLikeStaticMeshActors(UPackage* Package, UWorld* World)
{
    if (!Package || !World) { return 0; }

    TArray<AActor*> ToRemove;
    for (TObjectIterator<AStaticMeshActor> It; It; ++It)
    {
        AStaticMeshActor* Actor = *It;
        if (Actor && Actor->GetOutermost() == Package && IsGroundLikeStaticMeshActor(Actor))
        {
            ToRemove.Add(Actor);
        }
    }

    for (AActor* Actor : ToRemove)
    {
        World->DestroyActor(Actor);
    }
    return ToRemove.Num();
}

static int32 RemoveStandaloneStaticMeshClutter(UPackage* Package, UWorld* World)
{
    if (!Package || !World) { return 0; }

    TArray<AActor*> ToRemove;
    for (TObjectIterator<AStaticMeshActor> It; It; ++It)
    {
        AStaticMeshActor* Actor = *It;
        if (Actor && Actor->GetOutermost() == Package)
        {
            ToRemove.Add(Actor);
        }
    }

    for (AActor* Actor : ToRemove)
    {
        World->DestroyActor(Actor);
    }
    return ToRemove.Num();
}

static bool BuildOneMap(
    const FString& MapName,
    bool bIncludeDrone,
    ESZTUMultirotorAirframe Airframe = ESZTUMultirotorAirframe::QuadX,
    bool bIncludeRover = false)
{
    const FString PackagePath = TEXT("/Game/Maps/") + MapName;

    // 先删除旧文件，避免 SavePackage 冲突
    FString OldFile = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    if (IFileManager::Get().FileExists(*OldFile))
    {
        IFileManager::Get().Delete(*OldFile);
    }

    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreatePackage failed: %s"), *PackagePath);
        return false;
    }
    Package->SetFlags(RF_Public | RF_Standalone);

    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*MapName), Package);
    if (!World)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreateWorld failed: %s"), *MapName);
        return false;
    }
    World->SetFlags(RF_Public | RF_Standalone);
    ConfigureWorldForRuntimeDemo(World);

    FActorSpawnParameters SP;
    SP.bNoFail = true;
    SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

    FVector Loc(0.f, 0.f, 0.f);
    FRotator Rot(0.f, 0.f, 0.f);

    if (bIncludeRover)
    {
        Loc = FVector(0.f, 0.f, 0.f);
        ASZTURoverPawn* Rover = World->SpawnActor<ASZTURoverPawn>(ASZTURoverPawn::StaticClass(), Loc, Rot, SP);
        if (Rover)
        {
            Rover->AutoPossessPlayer = EAutoReceiveInput::Player0;
            UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned Ackermann rover"));
        }
        else
        {
            UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn Ackermann rover"));
            return false;
        }
    }
    else if (bIncludeDrone)
    {
        Loc = FVector(0.f, 0.f, 120.f);
        ASZTUDronePawn* Drone = World->SpawnActor<ASZTUDronePawn>(ASZTUDronePawn::StaticClass(), Loc, Rot, SP);
        if (Drone)
        {
            Drone->Airframe = Airframe;
            // 关键：纯无人机场景关掉自动生成风场，避免 PIE 时被拉进会卡死的风场
            Drone->bAutoSpawnWindField = false;
            Drone->WindDragGain = 0.35f;
            Drone->AutoPossessPlayer = EAutoReceiveInput::Player0;
            UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned Drone (bAutoSpawnWindField=false)"));
        }
        else
        {
            UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn Drone"));
            return false;
        }
    }
    else
    {
        ASZTUWindFlag* Flag = World->SpawnActor<ASZTUWindFlag>(ASZTUWindFlag::StaticClass(), Loc, Rot, SP);
        if (Flag)
        {
            UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned Flag (standalone, no wind field)"));
        }
        else
        {
            UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn Flag"));
            return false;
        }
    }

    // PlayerStart
    Loc = FVector(0.f, 0.f, 0.f);
    World->SpawnActor<APlayerStart>(APlayerStart::StaticClass(), Loc, Rot, SP);

    // 光照（极简、低开销，且不在加载时触发任何捕获/死等）：仅方向光、关闭动态阴影。
    // 刻意不放置 SkyLight——其首启 cubemap 捕获会等待 shader 编译完成，在弱机上易表现为编辑器卡死。
    Loc = FVector(0.f, 0.f, 500.f); Rot = FRotator(-50.f, 0.f, 0.f);
    ADirectionalLight* Dir = World->SpawnActor<ADirectionalLight>(ADirectionalLight::StaticClass(), Loc, Rot, SP);
    if (Dir)
    {
        ConfigureDirectionalLight(Dir);  // 无 SkyLight 兜底，方向光给足亮度保证无人机清晰可见
    }

    // 地面（引擎内置 Plane，放大做参照）
    Loc = FVector::ZeroVector; Rot = FRotator::ZeroRotator;
    SpawnRepairedGroundPlane(World, SP, FVector(0.f, 0.f, 0.5f), 60.f);

    World->MarkPackageDirty();

    FString Filename = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, World, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));

    // 保存完成后清理 world，避免进程退出时 UWorld::FinishDestroy 崩溃（退出码 3）
    World->CleanupWorld();
    return bSaved;
}

static bool BuildFactoryFlightMap(const FString& MapName, bool bIncludeWind = false, int32 DroneCount = 1)
{
    const FString PackagePath = TEXT("/Game/Maps/") + MapName;
    const FString OldFile = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    if (IFileManager::Get().FileExists(*OldFile))
    {
        IFileManager::Get().Delete(*OldFile);
    }

    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreatePackage failed: %s"), *PackagePath);
        return false;
    }
    Package->SetFlags(RF_Public | RF_Standalone);

    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*MapName), Package);
    if (!World)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreateWorld failed: %s"), *MapName);
        return false;
    }
    World->SetFlags(RF_Public | RF_Standalone);
    ConfigureWorldForRuntimeDemo(World);

    FActorSpawnParameters SP;
    SP.bNoFail = true;
    SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

    ASZTUWindFieldActor* WindField = nullptr;
    ASZTUWindFlag* Flag = nullptr;
    if (bIncludeWind)
    {
        WindField = World->SpawnActor<ASZTUWindFieldActor>(
            ASZTUWindFieldActor::StaticClass(), FVector::ZeroVector,
            FRotator::ZeroRotator, SP);
        if (!WindField)
        {
            UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn factory wind wall."));
            World->CleanupWorld();
            return false;
        }
        WindField->bShowFanArray = true;
        WindField->bShowWindArrows = false;
        WindField->VisualizationStride = 8;
        if (WindField->FanCells)
        {
            WindField->FanCells->SetRelativeLocation(
                FVector(0.0f, 0.0f, WindField->ArrayHeightM * 50.0f));
        }
    }

    const FVector DroneLocation = bIncludeWind
        ? FVector(500.0f, 0.0f, 120.0f)
        : FVector(0.0f, 0.0f, 120.0f);
    // 双机 spawn：DroneCount>=2 时按 ±250cm 错开 X 位置，每个 DronePawn 设 DroneId
    const int32 ActualDroneCount = FMath::Max(1, DroneCount);
    for (int32 i = 0; i < ActualDroneCount; ++i)
    {
        const int32 ThisDroneId = i + 1;
        // 第 1 架在中心，第 2 架在 -250cm（-2.5m），第 3 架在 +250cm，依次类推
        const float XOffsetCm = (i == 0) ? 0.0f : ((i % 2 == 1) ? -250.0f : 250.0f) * float((i + 1) / 2);
        const FVector ThisDroneLocation = DroneLocation + FVector(XOffsetCm, 0.0f, 0.0f);
        ASZTUDronePawn* Drone = World->SpawnActor<ASZTUDronePawn>(
            ASZTUDronePawn::StaticClass(), ThisDroneLocation,
            FRotator::ZeroRotator, SP);
        if (!Drone)
        {
            UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn factory-scene drone #%d."), ThisDroneId);
            World->CleanupWorld();
            return false;
        }
        Drone->DroneId = ThisDroneId;
        Drone->Airframe = ESZTUMultirotorAirframe::QuadX;
        Drone->bAutoSpawnWindField = false;
        Drone->WindDragGain = 0.35f;
        if (i == 0)
        {
            Drone->AutoPossessPlayer = EAutoReceiveInput::Player0;
        }
        if (WindField)
        {
            Drone->WindFieldReference = WindField;
        }
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned drone #%d at X=%+.1f (DroneId=%d → TCP port %d)"),
            ThisDroneId, ThisDroneLocation.X, ThisDroneId, 4559 + ThisDroneId);
    }
    if (WindField)
    {
        Flag = World->SpawnActor<ASZTUWindFlag>(
            ASZTUWindFlag::StaticClass(), FVector(280.0f, 470.0f, 0.0f),
            FRotator::ZeroRotator, SP);
        if (Flag)
        {
            Flag->WindFieldReference = WindField;
        }
    }

    World->SpawnActor<APlayerStart>(
        APlayerStart::StaticClass(), FVector(DroneLocation.X, DroneLocation.Y, 0.0f),
        FRotator::ZeroRotator, SP);

    const bool bGroundOk = SpawnFactoryTestPad(World, SP) != nullptr;

    const int32 BackWallCount = SpawnFactoryMeshLine(
        World, SP, TEXT("SM_Wall_1"), FVector(0.0f, 1120.0f, 0.0f),
        2700.0f, true);
    const int32 LeftFenceCount = SpawnFactoryMeshLine(
        World, SP, TEXT("SM_Fence_1"), FVector(-1420.0f, 100.0f, 0.0f),
        1700.0f, false);
    const int32 RightFenceCount = SpawnFactoryMeshLine(
        World, SP, TEXT("SM_Fence_1"), FVector(1420.0f, 100.0f, 0.0f),
        1700.0f, false);

    const bool bPropsOk =
        SpawnFactoryMesh(World, SP, TEXT("SM_Container_1"), FVector(-1060.0f, 760.0f, 0.0f), FRotator(0.0f, 12.0f, 0.0f)) &&
        SpawnFactoryMesh(World, SP, TEXT("SM_Container_2"), FVector(1050.0f, 780.0f, 0.0f), FRotator(0.0f, 170.0f, 0.0f)) &&
        SpawnFactoryMesh(World, SP, TEXT("SM_Hut_1"), FVector(-1180.0f, -720.0f, 0.0f), FRotator(0.0f, 95.0f, 0.0f)) &&
        SpawnFactoryMesh(World, SP, TEXT("SM_Pallet_1"), FVector(1120.0f, -760.0f, 0.0f), FRotator(0.0f, -18.0f, 0.0f)) &&
        SpawnFactoryMesh(World, SP, TEXT("SM_Barrel_1"), FVector(1210.0f, -650.0f, 0.0f));

    ASkyAtmosphere* Sky = World->SpawnActor<ASkyAtmosphere>(
        ASkyAtmosphere::StaticClass(), FVector::ZeroVector,
        FRotator::ZeroRotator, SP);

    ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(
        ADirectionalLight::StaticClass(), FVector(0.0f, 0.0f, 600.0f),
        FRotator(-48.0f, -35.0f, 0.0f), SP);
    if (Sun)
    {
        ConfigureDirectionalLight(Sun);
        if (UDirectionalLightComponent* SunComponent =
            Cast<UDirectionalLightComponent>(Sun->GetLightComponent()))
        {
            SunComponent->SetAtmosphereSunLight(true);
            SunComponent->SetLightColor(FLinearColor(1.0f, 0.92f, 0.80f));
        }
    }

    APointLight* FrontFill = World->SpawnActor<APointLight>(
        APointLight::StaticClass(), FVector(0.0f, -650.0f, 800.0f),
        FRotator::ZeroRotator, SP);
    if (FrontFill && FrontFill->PointLightComponent)
    {
        UPointLightComponent* Component = FrontFill->PointLightComponent;
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetCastShadows(false);
        Component->SetIntensity(9000.0f);
        Component->SetAttenuationRadius(2800.0f);
        Component->SetLightColor(FLinearColor(1.0f, 0.93f, 0.84f));
    }

    APointLight* SideFill = World->SpawnActor<APointLight>(
        APointLight::StaticClass(), FVector(1050.0f, 650.0f, 650.0f),
        FRotator::ZeroRotator, SP);
    if (SideFill && SideFill->PointLightComponent)
    {
        UPointLightComponent* Component = SideFill->PointLightComponent;
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetCastShadows(false);
        Component->SetIntensity(5500.0f);
        Component->SetAttenuationRadius(2200.0f);
        Component->SetLightColor(FLinearColor(0.70f, 0.80f, 1.0f));
    }

    const bool bWindActorsOk = !bIncludeWind || (WindField && Flag);
    const bool bLayoutOk = bGroundOk && Sky && Sun && FrontFill && SideFill && bPropsOk &&
        bWindActorsOk &&
        BackWallCount > 0 && LeftFenceCount > 0 && RightFenceCount > 0;
    UE_LOG(LogTemp, Display,
        TEXT("[BuildMaps] Factory park layout: wind=%s walls=%d leftFences=%d rightFences=%d status=%s"),
        bIncludeWind ? TEXT("true") : TEXT("false"),
        BackWallCount, LeftFenceCount, RightFenceCount,
        bLayoutOk ? TEXT("OK") : TEXT("INCOMPLETE"));

    World->MarkPackageDirty();
    const FString Filename = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, World, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));

    World->CleanupWorld();
    return bLayoutOk && bSaved;
}

static bool BuildCombinedMap(const FString& MapName)
{
    const FString PackagePath = TEXT("/Game/Maps/") + MapName;

    FString OldFile = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    if (IFileManager::Get().FileExists(*OldFile))
    {
        IFileManager::Get().Delete(*OldFile);
    }

    UPackage* Package = CreatePackage(*PackagePath);
    if (!Package)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreatePackage failed: %s"), *PackagePath);
        return false;
    }
    Package->SetFlags(RF_Public | RF_Standalone);

    UWorld* World = UWorld::CreateWorld(EWorldType::Editor, false, FName(*MapName), Package);
    if (!World)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] CreateWorld failed: %s"), *MapName);
        return false;
    }
    World->SetFlags(RF_Public | RF_Standalone);
    ConfigureWorldForRuntimeDemo(World);

    FActorSpawnParameters SP;
    SP.bNoFail = true;
    SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

    FVector Loc(0.f, 0.f, 0.f);
    FRotator Rot(0.f, 0.f, 0.f);

    // 1) 风场放在原点，风沿 +X
    ASZTUWindFieldActor* WindField = World->SpawnActor<ASZTUWindFieldActor>(ASZTUWindFieldActor::StaticClass(), Loc, Rot, SP);
    if (!WindField)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn WindField for combined map"));
        return false;
    }
    WindField->bShowFanArray = true;
    WindField->bShowWindArrows = false;  // 编辑器视口不画箭头，避免卡死
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned WindField for combined map"));

    // 2) 无人机放在风场前方 (+X 方向) 约 5m、高 1.5m
    Loc = FVector(500.f, 0.f, 150.f);
    ASZTUDronePawn* Drone = World->SpawnActor<ASZTUDronePawn>(ASZTUDronePawn::StaticClass(), Loc, Rot, SP);
    if (!Drone)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn Drone for combined map"));
        return false;
    }
    Drone->WindFieldReference = WindField;       // 显式引用，不自动生成分散风场
    Drone->bAutoSpawnWindField = false;
    Drone->WindDragGain = 0.35f;
    Drone->AutoPossessPlayer = EAutoReceiveInput::Player0;
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned Drone (WindFieldReference assigned)"));

    // 3) 旗子放在侧前方，便于同时观察
    Loc = FVector(300.f, 250.f, 0.f);
    ASZTUWindFlag* Flag = World->SpawnActor<ASZTUWindFlag>(ASZTUWindFlag::StaticClass(), Loc, Rot, SP);
    if (!Flag)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to spawn Flag for combined map"));
        return false;
    }
    Flag->WindFieldReference = WindField;
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Spawned Flag (WindFieldReference assigned)"));

    // PlayerStart（放在无人机附近，方便进入 PIE 观察）
    Loc = FVector(450.f, 0.f, 0.f);
    World->SpawnActor<APlayerStart>(APlayerStart::StaticClass(), Loc, Rot, SP);

    // 方向光
    Loc = FVector(0.f, 0.f, 500.f); Rot = FRotator(-50.f, 0.f, 0.f);
    ADirectionalLight* Dir = World->SpawnActor<ADirectionalLight>(ADirectionalLight::StaticClass(), Loc, Rot, SP);
    if (Dir)
    {
        ConfigureDirectionalLight(Dir);
    }

    // 地面
    Loc = FVector::ZeroVector; Rot = FRotator::ZeroRotator;
    SpawnRepairedGroundPlane(World, SP, FVector(250.f, 0.f, 0.5f), 80.f);

    World->MarkPackageDirty();

    FString Filename = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, World, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));

    World->CleanupWorld();
    return bSaved;
}

static bool AddFlagAndDroneToDemoScene(const FString& MapName)
{
    const FString PackagePath = TEXT("/Game/Maps/") + MapName;

    UPackage* Package = LoadPackage(nullptr, *PackagePath, LOAD_None);
    if (!Package)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to load package: %s"), *PackagePath);
        return false;
    }

    UWorld* World = UWorld::FindWorldInPackage(Package);
    if (!World)
    {
        World = FindObject<UWorld>(Package, TEXT("World"));
    }
    if (!World)
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] No world in package: %s"), *PackagePath);
        return false;
    }

    // 用 TObjectIterator 按包过滤查找（绕过 NullRHI 下 World 迭代器未初始化的问题）
    auto InThisPackage = [Package](const UObject* O) { return O && O->GetOutermost() == Package; };

    ConfigureWorldForRuntimeDemo(World);

    ASZTUWindFieldActor* WindField = nullptr;
    for (TObjectIterator<ASZTUWindFieldActor> It; It; ++It)
    {
        if (InThisPackage(*It)) { WindField = *It; break; }
    }
    if (WindField)
    {
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Found WindField in %s (TObjectIterator)"), *MapName);
    }
    else
    {
        UE_LOG(LogTemp, Warning, TEXT("[BuildMaps] WindField not found in %s; new actors rely on global lookup"), *MapName);
    }

    // 删除包内已有的风场/无人机/旗子，替换为当前修好的运行时版本。
    TArray<AActor*> ToRemove;
    for (TObjectIterator<ASZTUWindFieldActor> It; It; ++It) { if (InThisPackage(*It)) ToRemove.Add(*It); }
    for (TObjectIterator<ASZTUDronePawn> It; It; ++It) { if (InThisPackage(*It)) ToRemove.Add(*It); }
    for (TObjectIterator<ASZTUWindFlag> It; It; ++It) { if (InThisPackage(*It)) ToRemove.Add(*It); }
    for (AActor* A : ToRemove) { World->DestroyActor(A); }
    if (ToRemove.Num() > 0)
    {
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Removed %d old wind/drone/flag actor(s) from %s"), ToRemove.Num(), *MapName);
    }

    FActorSpawnParameters SP;
    SP.bNoFail = true;
    SP.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

    FRotator Rot(0.f, 0.f, 0.f);

    FVector Loc(0.f, 0.f, 0.f);
    WindField = World->SpawnActor<ASZTUWindFieldActor>(ASZTUWindFieldActor::StaticClass(), Loc, Rot, SP);
    if (WindField)
    {
        WindField->bShowFanArray = true;
        WindField->VisualizationStride = 8;
        WindField->bShowWindArrows = false;
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Added (repaired) WindField to %s"), *MapName);
    }
    else
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to add WindField to %s"), *MapName);
        return false;
    }

    // 放回修好的无人机（同 DroneTestMap 同款）：风场前方 (+X) 约 5m、高 1.5m
    Loc = FVector(500.f, 0.f, 150.f);
    ASZTUDronePawn* Drone = World->SpawnActor<ASZTUDronePawn>(ASZTUDronePawn::StaticClass(), Loc, Rot, SP);
    if (Drone)
    {
        if (WindField) Drone->WindFieldReference = WindField;
        Drone->bAutoSpawnWindField = false;
        Drone->WindDragGain = 0.35f;
        Drone->AutoPossessPlayer = EAutoReceiveInput::Player0;
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Added (repaired) Drone to %s"), *MapName);
    }
    else
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to add Drone to %s"), *MapName);
        return false;
    }

    // 放回修好的旗子（同 FlagTestMap 同款）
    Loc = FVector(350.f, -220.f, 0.f);
    ASZTUWindFlag* Flag = World->SpawnActor<ASZTUWindFlag>(ASZTUWindFlag::StaticClass(), Loc, Rot, SP);
    if (Flag)
    {
        if (WindField) Flag->WindFieldReference = WindField;
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Added (repaired) Flag to %s"), *MapName);
    }
    else
    {
        UE_LOG(LogTemp, Error, TEXT("[BuildMaps] Failed to add Flag to %s"), *MapName);
        return false;
    }

    // PlayerStart
    bool bHasPlayerStart = false;
    for (TObjectIterator<APlayerStart> It; It; ++It) { if (InThisPackage(*It)) { bHasPlayerStart = true; break; } }
    if (!bHasPlayerStart)
    {
        Loc = FVector(450.f, 0.f, 0.f);
        World->SpawnActor<APlayerStart>(APlayerStart::StaticClass(), Loc, Rot, SP);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Added PlayerStart to %s"), *MapName);
    }

    const int32 RemovedClutterActors = RemoveStandaloneStaticMeshClutter(Package, World);
    if (RemovedClutterActors > 0)
    {
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Removed %d old standalone static mesh clutter actor(s) from %s"), RemovedClutterActors, *MapName);
    }
    SpawnRepairedGroundPlane(World, SP, FVector(250.f, 0.f, 0.5f), 100.f);

    for (TObjectIterator<ADirectionalLight> It; It; ++It)
    {
        if (InThisPackage(*It)) { ConfigureDirectionalLight(*It); }
    }

    World->MarkPackageDirty();

    FString Filename = FPaths::ProjectContentDir() / TEXT("Maps") / MapName + TEXT(".umap");
    FSavePackageArgs SaveArgs;
    SaveArgs.Error = GError;
    SaveArgs.bForceByteSwapping = false;
    SaveArgs.bWarnOfLongFilename = true;
    SaveArgs.SaveFlags = SAVE_None;
    const bool bSaved = UPackage::SavePackage(Package, World, *Filename, SaveArgs);
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Saved %s -> %s : %s"),
        *PackagePath, *Filename, bSaved ? TEXT("OK") : TEXT("FAILED"));

    return bSaved;
}

int32 USZTUWindFieldBuildMapsCommandlet::Main(const FString& Params)
{
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] 开始无头生成独立关卡 ..."));
    const FLinearColor FlagFabricRed = FLinearColor::FromSRGBColor(FColor(150, 31, 32));

    if (FParse::Param(*Params, TEXT("FlagMaterialOnly")))
    {
        const bool bFlagMaterialOk = CreateColoredMaterial(
            TEXT("M_FlagColor"), FlagFabricRed, false, 0.85f, 0.12f, 0.08f);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Flag material only: %s"),
            bFlagMaterialOk ? TEXT("OK") : TEXT("FAILED"));
        return bFlagMaterialOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("DroneMaterialOnly")))
    {
        // 机身+桨叶材质单独重建。v3：机身 emissive 0.08→0.20 让远处可见
        bool bDroneMatOk = true;
        bDroneMatOk &= CreateColoredMaterial(TEXT("M_DroneBodyColor"),
            FLinearColor(1.0f, 0.42f, 0.0f), false, 0.55f, 0.20f, 0.20f);
        bDroneMatOk &= CreateColoredMaterial(TEXT("M_DronePropellerColor"),
            FLinearColor(0.18f, 0.18f, 0.20f), false, 0.50f, 0.20f, 0.10f);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Drone material only: %s"),
            bDroneMatOk ? TEXT("OK") : TEXT("FAILED"));
        return bDroneMatOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("FanMaterialOnly")))
    {
        // 风墙扇叶材质单独重建。v2：颜色调暗 (0.05, 0.18, 0.20) 暗青灰 + emissive 0，
        // 让 80x40 扇阵不再"喧宾夺主"，远看是低调的格栅而不是抢眼的青色方块。
        const bool bFanMatOk = CreateColoredMaterial(
            TEXT("M_FanColor"), FLinearColor(0.05f, 0.18f, 0.20f), false, 0.85f, 0.05f, 0.0f);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Fan material only: %s"),
            bFanMatOk ? TEXT("OK") : TEXT("FAILED"));
        return bFanMatOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("FactoryMapOnly")))
    {
        const bool bFactoryMapOk = BuildFactoryFlightMap(TEXT("SZTUFactoryFlightScene"));
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Factory map only: %s"),
            bFactoryMapOk ? TEXT("OK") : TEXT("FAILED"));
        return bFactoryMapOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("FactoryDualMapOnly")))
    {
        // 双机场景：spawn 2 个 DronePawn (DroneId=1 黄色 + DroneId=2 青色)，共用一个风场
        // 端口分配：DroneId=1 → TCP 4560, DroneId=2 → TCP 4561
        const bool bDualMapOk = BuildFactoryFlightMap(TEXT("SZTUFactoryDualScene"), true, 2);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Factory dual map only: %s"),
            bDualMapOk ? TEXT("OK") : TEXT("FAILED"));
        return bDualMapOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("FactoryWindMapOnly")))
    {
        const bool bFactoryWindMapOk = BuildFactoryFlightMap(TEXT("SZTUFactoryWindScene"), true);
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Factory wind map only: %s"),
            bFactoryWindMapOk ? TEXT("OK") : TEXT("FAILED"));
        return bFactoryWindMapOk ? 0 : 1;
    }

    if (FParse::Param(*Params, TEXT("UrbanMapOnly")))
    {
        bool bUrbanOk = true;
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanGround"), FLinearColor(0.13f, 0.15f, 0.16f));
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanRoad"), FLinearColor(0.055f, 0.065f, 0.075f));
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanLane"), FLinearColor(0.88f, 0.78f, 0.20f), false, 0.72f, 0.05f, 0.05f);
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanBuilding"), FLinearColor(0.22f, 0.27f, 0.31f));
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanBuildingAlt"), FLinearColor(0.34f, 0.30f, 0.28f));
        bUrbanOk &= CreateColoredMaterial(TEXT("M_UrbanObstacle"), FLinearColor(0.68f, 0.18f, 0.10f));
        bUrbanOk &= CreateColoredMaterial(TEXT("M_SemanticStart"), FLinearColor(1.0f, 0.75f, 0.0f), true);
        bUrbanOk &= CreateColoredMaterial(TEXT("M_SemanticInspection"), FLinearColor(0.0f, 0.25f, 1.0f), true);
        bUrbanOk &= CreateColoredMaterial(TEXT("M_SemanticDelivery"), FLinearColor(0.0f, 0.85f, 0.15f), true);
        bUrbanOk &= CreateColoredMaterial(TEXT("M_SemanticMedical"), FLinearColor(1.0f, 0.05f, 0.03f), true);
        bUrbanOk &= BuildUrbanSemanticMap(TEXT("SZTUUrbanSemanticScene"));
        UE_LOG(LogTemp, Display, TEXT("[BuildMaps] Urban map only: %s"),
            bUrbanOk ? TEXT("OK") : TEXT("FAILED"));
        return bUrbanOk ? 0 : 1;
    }

    bool bOk = true;
    // 机身：橙色 + 自发光 0.35，让远处能看到一个亮橙点（factory-wind 场景运行时会被黄色 MID 覆盖 BaseColor，
    // 但 AmbientFill 参数由材质保留，MID 继承后仍有自发光）。
    // v2: AmbientFill 0.08 → 0.20，让远处能看到机身光点；桨叶保持深灰 + 轻微自发光。
    bOk &= CreateColoredMaterial(TEXT("M_DroneBodyColor"), FLinearColor(1.0f, 0.42f, 0.0f), false, 0.55f, 0.20f, 0.20f);
    // 桨叶：深灰 (0.18, 0.18, 0.20) + 轻微自发光 0.10，自然织物感
    bOk &= CreateColoredMaterial(TEXT("M_DronePropellerColor"), FLinearColor(0.18f, 0.18f, 0.20f), false, 0.50f, 0.20f, 0.10f);
    bOk &= CreateColoredMaterial(TEXT("M_FlagColor"), FlagFabricRed, false, 0.85f, 0.12f, 0.08f);
    bOk &= CreateColoredMaterial(TEXT("M_PoleColor"), FLinearColor(0.22f, 0.12f, 0.05f));
    bOk &= CreateColoredMaterial(TEXT("M_FanColor"), FLinearColor(0.05f, 0.18f, 0.20f), false, 0.85f, 0.05f, 0.0f);
    bOk &= CreateColoredMaterial(TEXT("M_GroundColor"), FLinearColor(0.16f, 0.18f, 0.18f));
    bOk &= CreateColoredMaterial(TEXT("M_UrbanGround"), FLinearColor(0.13f, 0.15f, 0.16f));
    bOk &= CreateColoredMaterial(TEXT("M_UrbanRoad"), FLinearColor(0.055f, 0.065f, 0.075f));
    bOk &= CreateColoredMaterial(TEXT("M_UrbanLane"), FLinearColor(0.88f, 0.78f, 0.20f), false, 0.72f, 0.05f, 0.05f);
    bOk &= CreateColoredMaterial(TEXT("M_UrbanBuilding"), FLinearColor(0.22f, 0.27f, 0.31f));
    bOk &= CreateColoredMaterial(TEXT("M_UrbanBuildingAlt"), FLinearColor(0.34f, 0.30f, 0.28f));
    bOk &= CreateColoredMaterial(TEXT("M_UrbanObstacle"), FLinearColor(0.68f, 0.18f, 0.10f));
    bOk &= CreateColoredMaterial(TEXT("M_SemanticStart"), FLinearColor(1.0f, 0.75f, 0.0f), true);
    bOk &= CreateColoredMaterial(TEXT("M_SemanticInspection"), FLinearColor(0.0f, 0.25f, 1.0f), true);
    bOk &= CreateColoredMaterial(TEXT("M_SemanticDelivery"), FLinearColor(0.0f, 0.85f, 0.15f), true);
    bOk &= CreateColoredMaterial(TEXT("M_SemanticMedical"), FLinearColor(1.0f, 0.05f, 0.03f), true);
    bOk &= BuildOneMap(TEXT("DroneTestMap"), true);
    bOk &= BuildOneMap(TEXT("SZTUFlightPatternsScene"), true);
    bOk &= BuildOneMap(TEXT("SZTUHexacopterScene"), true, ESZTUMultirotorAirframe::HexaX);
    bOk &= BuildOneMap(TEXT("SZTURoverScene"), false, ESZTUMultirotorAirframe::QuadX, true);
    bOk &= BuildOneMap(TEXT("FlagTestMap"), false);
    bOk &= BuildFactoryFlightMap(TEXT("SZTUFactoryFlightScene"));
    bOk &= BuildFactoryFlightMap(TEXT("SZTUFactoryWindScene"), true);
    bOk &= BuildFactoryFlightMap(TEXT("SZTUFactoryDualScene"), true, 2);  // 双机场景
    bOk &= BuildUrbanSemanticMap(TEXT("SZTUUrbanSemanticScene"));
    bOk &= BuildCombinedMap(TEXT("CombinedSceneMap"));
    bOk &= AddFlagAndDroneToDemoScene(TEXT("SZTUWindFieldDemoScene"));
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] 完成。结果=%s"), bOk ? TEXT("成功") : TEXT("失败"));
    return bOk ? 0 : 1;
}
