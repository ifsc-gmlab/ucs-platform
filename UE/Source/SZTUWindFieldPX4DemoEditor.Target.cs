using UnrealBuildTool;
using System.Collections.Generic;

public class SZTUWindFieldPX4DemoEditorTarget : TargetRules
{
    public SZTUWindFieldPX4DemoEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("SZTUWindFieldPX4Demo");
    }
}
