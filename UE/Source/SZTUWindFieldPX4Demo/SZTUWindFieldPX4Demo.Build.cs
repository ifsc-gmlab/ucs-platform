using System.IO;
using UnrealBuildTool;

public class SZTUWindFieldPX4Demo : ModuleRules
{
    public SZTUWindFieldPX4Demo(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "InputCore",
            "Sockets",
            "Networking",
            "Json",
            "ProceduralMeshComponent",
            "UMG",
            "Slate",
            "SlateCore"
        });

        // MAVLink C 头文件（从 PX4 build 拷贝，用于 SITL 仿真器接口）
        string MavlinkDir = Path.GetFullPath(Path.Combine(ModuleDirectory, "..", "ThirdParty", "MAVLink"));
        PublicIncludePaths.Add(MavlinkDir);
        PublicIncludePaths.Add(Path.Combine(MavlinkDir, "common"));
    }
}
