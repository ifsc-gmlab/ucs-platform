#pragma once

#include "CoreMinimal.h"
#include "Blueprint/UserWidget.h"
#include "Styling/SlateTypes.h"
#include "SZTUWindControlWidget.generated.h"

class ASZTUWindFieldActor;
class STextBlock;

UCLASS()
class SZTUWINDFIELDPX4DEMO_API USZTUWindControlWidget : public UUserWidget
{
    GENERATED_BODY()

public:
    void ConfigureWindField(ASZTUWindFieldActor* InWindField);

protected:
    virtual TSharedRef<SWidget> RebuildWidget() override;

private:
    void HandleWindSpeedChanged(float SliderValue);
    float GetSliderValue() const;
    void RefreshLabel(float WindSpeedMps);

    TWeakObjectPtr<ASZTUWindFieldActor> WindField;
    TSharedPtr<STextBlock> WindSpeedLabel;
    FSliderStyle LargeSliderStyle;
    float MaxWindSpeedMps = 20.0f;
};
