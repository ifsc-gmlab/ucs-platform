using UnrealBuildTool;
using System.Collections.Generic;

public class SZTUWindFieldPX4DemoTarget : TargetRules
{
    public SZTUWindFieldPX4DemoTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("SZTUWindFieldPX4Demo");
    }
}
