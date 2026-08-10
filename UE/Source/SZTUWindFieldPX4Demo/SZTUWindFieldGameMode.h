#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "SZTUWindFieldGameMode.generated.h"

class USZTUWindControlWidget;

UCLASS()
class SZTUWINDFIELDPX4DEMO_API ASZTUDualCameraHUD : public AHUD
{
    GENERATED_BODY()

public:
    virtual void DrawHUD() override;
};

UCLASS()
class SZTUWINDFIELDPX4DEMO_API ASZTUWindFieldGameMode : public AGameModeBase
{
    GENERATED_BODY()

public:
    ASZTUWindFieldGameMode();
    virtual void StartPlay() override;
    virtual void InitGame(const FString& MapName, const FString& Options, FString& ErrorMessage) override;

    // 场景组合开关（默认关闭 = 场景 / 旗子 / 无人机各自独立，由关卡自行摆放）。
    // 如需回退“打开即自动填充”的便利，可在关卡 World Settings 里把对应 GameMode
    // 子类/蓝图实例的开关打开。
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Level Population")
    bool bAutoSpawnWindField = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Level Population")
    bool bAutoSpawnFlag = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Observer Camera")
    bool bUseObserverCamera = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Observer Camera")
    FVector ObserverCameraLocation = FVector(680.0f, -650.0f, 320.0f);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Observer Camera")
    FVector ObserverCameraLookAt = FVector(280.0f, 80.0f, 95.0f);

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Observer Camera")
    float ObserverCameraFOV = 76.0f;

private:
    UPROPERTY(Transient)
    USZTUWindControlWidget* WindControlWidget = nullptr;
};
