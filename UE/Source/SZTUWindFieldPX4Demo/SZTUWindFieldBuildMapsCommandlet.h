#pragma once

#include "Commandlets/Commandlet.h"
#include "SZTUWindFieldBuildMapsCommandlet.generated.h"

UCLASS()
class USZTUWindFieldBuildMapsCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    USZTUWindFieldBuildMapsCommandlet();
    virtual int32 Main(const FString& Params) override;
};
