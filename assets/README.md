# Mesh and image assets

Ready-to-place geometry for Casa Monarch. Every file here is CC0 or CC-BY (attribution
recorded below and in each sidecar JSON). Nothing from 3D Warehouse or of unknown licence.
Total size about 2 MB.

## Conventions

- OBJ, **metres, Z up**, triangulated, no MTL/UV/normals; colour comes from the JSON that
  places the mesh (`rgb` in the sidecar is a sensible default).
- `origin=wall` files: x centred, `y_min = 0` at the **back** (wall side), `z_min = 0`,
  the front faces **+Y**. Place them with their `at` on the wall line and `rotation_z`
  so +Y points into the room.
- `origin=center` files: x and y centred, `z_min = 0`; free-standing. Where the object has
  a front (lamp head, bust face) it faces +Y; the teapot spout points +X.
- Each `<name>.obj` has a sidecar `<name>.json`:
  `{"file", "size_m": [x,y,z], "origin": "wall|center", "rgb": [r,g,b] sRGB 0..1,
  "parts": [...optional...], "props": {Product, MeshSource, MeshURL, MeshLicense,
  MeshAuthor, Dimensions, MeshNote}}`. Copy `props` into the IFC object's property set so
  the licence travels with the model.
- Split assets (`parts`): one OBJ per source material, all in the same frame, so a product
  keeps two colours while staying one IFC object. Use all part files together.
- Filenames carry the main size in mm (`sofa_polyhaven_2730` = 2.73 m long).
- Plan reference: `{"mesh": "assets/<file>.obj", "at": [x,y,z], "rotation_z": deg}`.

## Provenance

| Asset | Source | Author | Licence | Attribution required | Size (m) x,y,z |
|---|---|---|---|---|---|
| `sofa_polyhaven_2730.obj` | [Poly Haven "Sofa 03"](https://polyhaven.com/a/sofa_03) | Fran Calvente | CC0 1.0 | no | 2.731 x 0.925 x 1.118 |
| `armchair_polyhaven_820_legs.obj` + `_pillow.obj` | [Poly Haven "Modern Arm Chair 01"](https://polyhaven.com/a/modern_arm_chair_01) | Vibrant Nordic | CC0 1.0 | no | 0.820 x 0.987 x 1.023 |
| `dining_table_polyhaven_2400.obj` | [Poly Haven "Painted Wooden Table"](https://polyhaven.com/a/painted_wooden_table) | Kirill Sannikov | CC0 1.0 | no | 2.406 x 1.137 x 0.959 |
| `dining_chair_polyhaven_430.obj` | [Poly Haven "Painted Wooden Chair 01"](https://polyhaven.com/a/painted_wooden_chair_01) | Kuutti Siitonen | CC0 1.0 | no | 0.432 x 0.540 x 0.956 |
| `bed_polyhaven_1500x2040.obj` | [Poly Haven "Gothic Bed 01"](https://polyhaven.com/a/GothicBed_01) | Kirill Sannikov | CC0 1.0 | no | 1.494 x 2.042 x 1.535 |
| `potted_plant_polyhaven_700_pot.obj` + `_leaves.obj` | [Poly Haven "Potted Plant 02"](https://polyhaven.com/a/potted_plant_02) | Rico Cilliers | CC0 1.0 | no | 0.701 x 0.658 x 0.842 |
| `desk_lamp_polyhaven_890.obj` | [Poly Haven "Desk Lamp Arm 01"](https://polyhaven.com/a/desk_lamp_arm_01) | Kuutti Siitonen (model), Yann Kervran (rig) | CC0 1.0 | no | 0.203 x 0.616 x 0.893 |
| `sideboard_polyhaven_2440.obj` | [Poly Haven "Modern Wooden Cabinet"](https://polyhaven.com/a/modern_wooden_cabinet) | Patrik Pangerl | CC0 1.0 | no | 2.440 x 0.520 x 0.680 |
| `coffee_table_polyhaven_1300.obj` | [Poly Haven "Coffee Table Round 01"](https://polyhaven.com/a/coffee_table_round_01) | Ulan Cabanilla | CC0 1.0 | no | 1.302 x 1.302 x 0.491 |
| `bust_polyhaven_515.obj` | [Poly Haven "Marble Bust 01"](https://polyhaven.com/a/marble_bust_01) | Rico Cilliers | CC0 1.0 | no | 0.272 x 0.300 x 0.515 |
| `teapot_sketchfab_260.obj` | [Sketchfab "Old Soviet teapot"](https://sketchfab.com/3d-models/old-soviet-teapot-9b087673052446299e6decda222a42da) | [Amagitsune](https://sketchfab.com/Amagitsune) | [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) | **yes**, see below | 0.260 x 0.186 x 0.316 |
| `picture_great_wave.jpg` | [Wikimedia Commons "The Great Wave off Kanagawa"](https://commons.wikimedia.org/wiki/File:The_Great_Wave_off_Kanagawa.jpg) | Katsushika Hokusai (1760-1849) | Public domain (copyright expired; faithful reproduction of a 2D work) | no | 1200 x 828 px, aspect 1.449 |

Sketchfab attribution text (keep it on the plaque and in the IFC props):
"This work is based on "Old Soviet teapot" (https://sketchfab.com/3d-models/old-soviet-teapot-9b087673052446299e6decda222a42da)
by Amagitsune (https://sketchfab.com/Amagitsune) licensed under CC-BY-4.0 (http://creativecommons.org/licenses/by/4.0/)".

The picture is 1200 x 828 px (aspect 1.449). A frame of 0.90 x 0.62 m or 0.60 x 0.41 m keeps the
proportions; the original print is about 0.26 x 0.38 m. Reference it with `texture` on a thin box.

Poly Haven sizes are the authors' real-world dimensions (the API's `dimensions`, in mm), so no
`fit_*` was needed. The Sketchfab teapot has no real scale; it was fitted to 0.26 m width
(a typical 3 l enamel kettle). The desk lamp is a clamp-on model: z = 0 is the bottom of the
clamp, so either stand it as is or sink it about 0.09 m below the table top.

## How each file was made

Download, then convert with headless Blender (`src/convert_asset.py`, prints the bbox):

```
# Poly Haven: pick the 1k glTF and its .bin from the files API (User-Agent needed)
curl -sL -A Mozilla/5.0 https://api.polyhaven.com/files/sofa_03        # -> gltf.1k.gltf.url + include .bin
curl -sL -A Mozilla/5.0 https://api.polyhaven.com/info/sofa_03         # -> authors, dimensions (mm)

# Sketchfab: the download endpoint needs a token and returns a signed zip URL (valid 5 min)
curl -s -H "Authorization: Token $SKETCHFAB" https://api.sketchfab.com/v3/models/<uid>/download
# unzip into an empty folder; convert scene.gltf; license.txt holds the attribution line

B=/Applications/Blender.app/Contents/MacOS/Blender
$B -b --python src/convert_asset.py -- sofa_03.gltf             assets/sofa_polyhaven_2730.obj        rot_z=180 origin=wall   decimate=6000
$B -b --python src/convert_asset.py -- modern_arm_chair_01.gltf assets/armchair_polyhaven_820.obj     rot_z=180 origin=wall   decimate=5000 split=material
$B -b --python src/convert_asset.py -- painted_wooden_table.gltf assets/dining_table_polyhaven_2400.obj          origin=center decimate=2000
$B -b --python src/convert_asset.py -- painted_wooden_chair_01.gltf assets/dining_chair_polyhaven_430.obj rot_z=180 origin=wall decimate=2000
$B -b --python src/convert_asset.py -- GothicBed_01.gltf        assets/bed_polyhaven_1500x2040.obj    rot_z=180 origin=wall   decimate=5000
$B -b --python src/convert_asset.py -- potted_plant_02.gltf     assets/potted_plant_polyhaven_700.obj rot_z=180 origin=center decimate=8000 split=material
$B -b --python src/convert_asset.py -- desk_lamp_arm_01.gltf    assets/desk_lamp_polyhaven_890.obj    rot_z=180 origin=center decimate=3000
$B -b --python src/convert_asset.py -- modern_wooden_cabinet.gltf assets/sideboard_polyhaven_2440.obj rot_z=180 origin=wall   decimate=3000
$B -b --python src/convert_asset.py -- coffee_table_round_01.gltf assets/coffee_table_polyhaven_1300.obj        origin=center decimate=2000
$B -b --python src/convert_asset.py -- marble_bust_01.gltf      assets/bust_polyhaven_515.obj         rot_z=180 origin=center decimate=6000
$B -b --python src/convert_asset.py -- scene.gltf               assets/teapot_sketchfab_260.obj       rot_z=180 origin=center fit_x=0.26

# picture: 1200 px wide JPEG from Commons, re-encoded to stay under 400 kB
curl -sL -A Mozilla/5.0 "https://commons.wikimedia.org/wiki/Special:FilePath/The_Great_Wave_off_Kanagawa.jpg?width=1200" -o wave.jpg
sips -s format jpeg -s formatOptions 62 --resampleWidth 1200 wave.jpg --out assets/picture_great_wave.jpg
```

Poly Haven models face -Y, hence `rot_z=180` so the front faces +Y. After converting, the
split part files were renamed (`armchair_polyhaven_820_<material>.obj` to `_legs`/`_pillow`,
plant to `_pot`/`_leaves`), and every OBJ was post-processed to drop the unreferenced vertices
the decimate modifier leaves behind and to round coordinates to 0.1 mm (the sideboard shrank
from 496 to 172 kB that way). The `*_parts.json` files the converter writes were folded into
the sidecars (`parts`), with hand-picked colours: textured sources report white base colours.

Always look at the preview image before downloading, and read the printed `imported bbox`
before trusting a `fit_*`: scene exports can contain a floor plane or a figure that
dominates the box (`exclude=` drops them by name; the converter prints the inventory).

## Finding more

`uv run python src/find_asset.py "potted plant"` searches Poly Haven (CC0, real scale, small
catalogue) and, with `$SKETCHFAB` set, Sketchfab (mostly CC-BY, check the licence field).
3D Warehouse results are shown too, but its terms do not allow redistribution: convert such a
mesh for your own local model only and never commit it to a public repo.

## Without Blender

`src/convert_asset.py` needs Blender. Without it: `src/dae_to_obj.py` (pure Python) converts
Collada `.dae` exports, and for most furniture a few `boxes` authored in the storey JSON are
quicker and lighter than any download; ask your agent to "model it as boxes". The assets
here are already converted, so Casa Monarch builds without Blender.
