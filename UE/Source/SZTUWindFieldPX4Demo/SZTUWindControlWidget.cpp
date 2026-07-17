#include "SZTUWindControlWidget.h"

#include "SZTUWindFieldActor.h"
#include "Styling/AppStyle.h"
#include "Widgets/Input/SSlider.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

void USZTUWindControlWidget::ConfigureWindField(ASZTUWindFieldActor* InWindField)
{
    WindField = InWindField;
    RefreshLabel(InWindField ? InWindField->GetRuntimeWindSpeedMps() : 0.0f);
}

TSharedRef<SWidget> USZTUWindControlWidget::RebuildWidget()
{
    const float InitialWindMps = WindField.IsValid() ? WindField->GetRuntimeWindSpeedMps() : 0.0f;
    LargeSliderStyle = FAppStyle::Get().GetWidgetStyle<FSliderStyle>(TEXT("Slider"));
    LargeSliderStyle.SetBarThickness(8.0f);
    FSlateBrush NormalThumb = LargeSliderStyle.NormalThumbImage;
    NormalThumb.ImageSize = FVector2D(26.0f, 26.0f);
    LargeSliderStyle.SetNormalThumbImage(NormalThumb);
    FSlateBrush HoveredThumb = LargeSliderStyle.HoveredThumbImage;
    HoveredThumb.ImageSize = FVector2D(32.0f, 32.0f);
    LargeSliderStyle.SetHoveredThumbImage(HoveredThumb);

    TSharedRef<SWidget> Widget =
        SNew(SBox)
        .WidthOverride(420.0f)
        .HeightOverride(130.0f)
        [
            SNew(SBorder)
            .Padding(FMargin(18.0f, 14.0f))
            .BorderBackgroundColor(FLinearColor(0.025f, 0.03f, 0.035f, 0.92f))
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot()
                .AutoHeight()
                [
                    SAssignNew(WindSpeedLabel, STextBlock)
                    .Font(FCoreStyle::GetDefaultFontStyle("Bold", 20))
                    .ColorAndOpacity(FSlateColor(FLinearColor(0.92f, 0.96f, 0.98f)))
                    .Text(FText::FromString(FString::Printf(TEXT("Current wind: %.1f m/s"), InitialWindMps)))
                ]
                + SVerticalBox::Slot()
                .AutoHeight()
                .Padding(FMargin(0.0f, 10.0f, 0.0f, 0.0f))
                [
                    SNew(SBox)
                    .HeightOverride(44.0f)
                    [
                        SNew(SSlider)
                        .Style(&LargeSliderStyle)
                        .StepSize(0.025f)
                        .MouseUsesStep(true)
                        .Value(GetSliderValue())
                        .OnValueChanged(FOnFloatValueChanged::CreateUObject(this, &USZTUWindControlWidget::HandleWindSpeedChanged))
                    ]
                ]
            ]
        ];

    return Widget;
}

void USZTUWindControlWidget::HandleWindSpeedChanged(float SliderValue)
{
    const float WindSpeedMps = FMath::Clamp(SliderValue, 0.0f, 1.0f) * MaxWindSpeedMps;
    if (WindField.IsValid())
    {
        WindField->SetRuntimeWindSpeedMps(WindSpeedMps);
    }
    RefreshLabel(WindSpeedMps);
}

float USZTUWindControlWidget::GetSliderValue() const
{
    const float WindSpeedMps = WindField.IsValid() ? WindField->GetRuntimeWindSpeedMps() : 0.0f;
    return FMath::Clamp(WindSpeedMps / MaxWindSpeedMps, 0.0f, 1.0f);
}

void USZTUWindControlWidget::RefreshLabel(float WindSpeedMps)
{
    if (WindSpeedLabel.IsValid())
    {
        WindSpeedLabel->SetText(FText::FromString(FString::Printf(TEXT("Current wind: %.1f m/s"), WindSpeedMps)));
    }
}
