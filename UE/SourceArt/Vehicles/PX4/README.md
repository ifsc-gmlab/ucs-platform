# PX4 Vehicle Source Assets

These source assets are archived for the SZTU UE5/PX4 simulator. Generated OBJ files are development artifacts used by Unreal Engine; the original meshes and model descriptions remain the source of truth.

## Hexacopter

- Model: Typhoon H480 (six rotors)
- Source repository: `https://github.com/PX4/PX4-SITL_gazebo-classic.git`
- Source commit: `f835e077d06eaf09a57d5152fcfb85244b53b77a`
- Original path: `models/typhoon_h480`
- Archived files: body STL, CW/CCW prop assembly STL, `model.config`, and `typhoon_h480.sdf.jinja`

## Rover

- Model: Generic Ackermann Rover
- Source repository: `https://github.com/PX4/PX4-Autopilot.git`
- Source commit: `a86306a146c9dd1e33a7677fb7f5a855bf2c1c9f`
- Original path: `Tools/simulation/gz/models/rover_ackermann`
- Archived files: body/wheel DAE, `model.config`, and `model.sdf`

## License

PX4 source and model files are distributed under the BSD 3-Clause license archived as `PX4_LICENSE_BSD-3-Clause.txt`. Preserve this README and the license when redistributing the UE project or generated assets.

## Regeneration

```powershell
python -m pip install trimesh pycollada
python .\Scripts\prepare_px4_vehicle_assets.py

& 'D:\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe' `
  'D:\SZTUWindFieldPX4Demo\Unreal\SZTUWindFieldPX4Demo.uproject' `
  -ExecutePythonScript='D:\SZTUWindFieldPX4Demo\Unreal\Scripts\import_px4_vehicle_models.py' `
  -unattended -nop4 -nosplash -nullrhi
```

Importing these meshes does not by itself change PX4 airframes or vehicle physics. Hexacopter actuator allocation and Rover steering/drive dynamics must be validated separately before claiming experiment parity.
