#include "SZTUWindFieldBuildMapsCommandlet.h"

#include "Engine/World.h"
#include "Engine/Level.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "GameFramework/PlayerStart.h"
#include "GameFramework/WorldSettings.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Components/StaticMeshComponent.h"
#include "Components/LightComponent.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionScalarParameter.h"
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

static bool CreateColoredMaterial(const FString& MatName, const FLinearColor& DefaultColor, bool bUnlit = false, float Roughness = 0.88f, float Specular = 0.08f)
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
    bool bOk = true;
    bOk &= CreateColoredMaterial(TEXT("M_DroneBodyColor"), FLinearColor(1.0f, 0.42f, 0.0f));
    bOk &= CreateColoredMaterial(TEXT("M_DronePropellerColor"), FLinearColor(0.04f, 0.04f, 0.05f));
    bOk &= CreateColoredMaterial(TEXT("M_FlagColor"), FLinearColor(0.50f, 0.025f, 0.015f), true, 0.98f, 0.0f);
    bOk &= CreateColoredMaterial(TEXT("M_PoleColor"), FLinearColor(0.22f, 0.12f, 0.05f));
    bOk &= CreateColoredMaterial(TEXT("M_FanColor"), FLinearColor(0.1f, 0.55f, 0.65f));
    bOk &= CreateColoredMaterial(TEXT("M_GroundColor"), FLinearColor(0.16f, 0.18f, 0.18f));
    bOk &= BuildOneMap(TEXT("DroneTestMap"), true);
    bOk &= BuildOneMap(TEXT("SZTUFlightPatternsScene"), true);
    bOk &= BuildOneMap(TEXT("SZTUHexacopterScene"), true, ESZTUMultirotorAirframe::HexaX);
    bOk &= BuildOneMap(TEXT("SZTURoverScene"), false, ESZTUMultirotorAirframe::QuadX, true);
    bOk &= BuildOneMap(TEXT("FlagTestMap"), false);
    bOk &= BuildCombinedMap(TEXT("CombinedSceneMap"));
    bOk &= AddFlagAndDroneToDemoScene(TEXT("SZTUWindFieldDemoScene"));
    UE_LOG(LogTemp, Display, TEXT("[BuildMaps] 完成。结果=%s"), bOk ? TEXT("成功") : TEXT("失败"));
    return bOk ? 0 : 1;
}
