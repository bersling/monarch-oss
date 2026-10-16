"""The house index: data/house.json names the project, the IFC output and the storeys.

Every tool reads storey codes, names, elevations and plan files from here; nothing in
src/ hard-codes a storey. Contract:

    {"name": "Casa Monarch", "ifc_file": "generated/casa_monarch.ifc", "units": "meters",
     "storeys": [{"code": "GF", "name": "Ground floor", "elevation": 0.0, "file": "ground_floor.json"},
                 {"code": "UF", "name": "Upper floor", "elevation": 2.9, "file": "upper_floor.json"}],
     "site": "site.json"}                       # optional

`code` is the storey key used by ./q --storey, element keys (NAME@GF), rules and the
viewer buttons. Storey order is list order.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = 'SITE'          # pseudo storey code for everything contained in the IfcSite


class House:
    def __init__(self, data, root=ROOT):
        self.root = Path(root)
        self.data = data
        self.name = data.get('name', 'House')
        self.description = data.get('description', '')
        self.ifc_file = data.get('ifc_file', 'generated/house.ifc')
        self.storeys = list(data['storeys'])
        if not self.storeys:
            raise ValueError('data/house.json: at least one storey is required')
        codes = [s['code'] for s in self.storeys]
        if len(set(codes)) != len(codes) or SITE in codes:
            raise ValueError(f'data/house.json: storey codes must be unique and not {SITE!r}: {codes}')
        self.codes = codes
        self.elevation = {s['code']: float(s['elevation']) for s in self.storeys}
        self.file = {s['code']: s['file'] for s in self.storeys}
        self.stem = {s['code']: Path(s['file']).stem for s in self.storeys}
        self.by_name = {s['name']: s['code'] for s in self.storeys}
        self.site_file = data.get('site')

    @property
    def ifc_path(self):
        return self.root / self.ifc_file

    def plan_path(self, code):
        return self.root / 'data' / self.file[code]

    def code(self, storey):
        """Accept a storey code, its name or its plan file (stem or name); return the code."""
        if storey in self.elevation:
            return storey
        if storey in self.by_name:
            return self.by_name[storey]
        stem = Path(str(storey)).stem
        for code, s in self.stem.items():
            if s == stem:
                return code
        raise KeyError(f'unknown storey {storey!r}; codes: {", ".join(self.codes)}')

    def default_code(self):
        """The storey at elevation 0 when there is one, else the lowest listed."""
        at_zero = [c for c, z in self.elevation.items() if abs(z) < 1e-9]
        return at_zero[0] if at_zero else min(self.elevation, key=self.elevation.get)

    def storey_of_z(self, z):
        """Code of the storey whose floor is at or below z (40 cm tolerance for finishes)."""
        below = [c for c, z0 in self.elevation.items() if z >= z0 - 0.4]
        return max(below, key=self.elevation.get) if below else min(self.elevation, key=self.elevation.get)

    def summary(self):
        return {'name': self.name, 'description': self.description, 'ifc_file': self.ifc_file,
                'storeys': [{'code': s['code'], 'name': s['name'], 'elevation': float(s['elevation'])}
                            for s in self.storeys]}


def load(root=ROOT):
    return House(json.loads((Path(root) / 'data/house.json').read_text()), root)


HOUSE = load()
