import os
import json
import re
from jinja2 import Template
from jinja2 import FileSystemLoader
from jinja2.environment import Environment


def to_c_macro(name):
    macro = re.sub("\W", "_", name)
    macro = macro + ("_" if macro[0].isdigit() else "")
    return macro.upper()


class SIGModel(object):
    def __init__(self, mid, name):
        self.mid = int(mid, 0)
        self.name = name


class VendorModel(object):
    def __init__(self, mid, cid, name):
        self.mid = int(mid, 0)
        self.cid = int(cid, 0)
        self.name = name

    @property
    def vmid(self):
        return self.mid << 16 + self.cid

    @property
    def macro_name(self):
        return to_c_macro(self.name)


class Source(object):
    def __init__(self, filename, group):
        self.filename = filename
        self.group = group

    def __eq__(self, other):
        if isinstance(other, Source):
            return (self.filename, self.group) == (other.filename, other.group)
        return NotImplemented

    def __neq__(self, other):
        return not (self == other)


class Element(object):
    def __init__(
        self, name, location, group=None, sig_models=[], vendor_models=[], sources=[]
    ):
        self.name = name
        self.location = int(location, 0)
        self.sig_models = [SIGModel(**m) for m in sig_models]
        self.vendor_models = self.check_vendor_models(vendor_models)
        self.sources = sources.copy()

    def check_vendor_models(self, vendor_models):
        vend_mod = [VendorModel(**m) for m in vendor_models]
        for m in vend_mod:
            i = vend_mod.index(m) + 1
            while i < len(vend_mod):
                n = vend_mod[i]
                if m.name == n.name:
                    print(f"\nBtMeshGenerator: In element: '{self.name}' ", end="")
                    print(f"duplicated vendor model name: '{m.name}' \n")
                    # exit('Duplicated vendor model name')
                    return vend_mod
                elif m.mid == n.mid:
                    print(f"\nBtMeshGenerator: In element: '{self.name}' ", end="")
                    print(f"duplicated vendor model ID (mid): {hex(m.mid)}\n")
                    # exit('Duplicated vendor model ID')
                    return vend_mod
                else:
                    i += 1
        return vend_mod

    @property
    def filenames(self):
        return [source.filename for source in self.sources]

    @property
    def num_s(self):
        return len(self.sig_models)

    @property
    def num_v(self):
        return len(self.vendor_models)

    # TODO: better
    @property
    def macros(self):
        return [
            to_c_macro("_".join([filename, self.name])) for filename in self.filenames
        ]

    @property
    def group_macros(self):
        # Remove duplicate entries from the list.
        # If a dcd file contributed to the same element more groups with the same
        # group name then entries could be duplicated. The group macro name and
        # value is the same so it can be tolerated.
        filtered_source_pairs = dict.fromkeys(
            (s.filename, s.group) for s in self.sources
        )
        # Generate group macro for each source which has explicit group name
        return [
            to_c_macro("_".join([src_pair[0], "group", src_pair[1], "elem_index"]))
            for src_pair in filtered_source_pairs.keys()
            if src_pair[1]
        ]

    def is_mergeable_with(self, other):
        if self.name != other.name:
            return False
        self_sig_models = {m.mid for m in self.sig_models}
        other_sig_models = {m.mid for m in other.sig_models}
        if not self_sig_models.isdisjoint(other_sig_models):
            return False
        self_vendor_models = {m.vmid for m in self.vendor_models}
        other_vendor_models = {m.vmid for m in other.vendor_models}
        if not self_vendor_models.isdisjoint(other_vendor_models):
            return False
        return True

    def merge_with(self, other):
        self.sig_models.extend(other.sig_models)
        self.vendor_models.extend(other.vendor_models)
        self.sources.extend(other.sources)


class DCD(object):
    def __init__(self, cid, pid, vid, elements=[]):
        self.cid = int(cid, 0)
        self.pid = int(pid, 0)
        self.vid = int(vid, 0)
        self.elements = [Element(**e) for e in elements]
        self.unique_vendor_models = []

    @property
    def total_models(self):
        return sum(elem.num_s + elem.num_v for elem in self.elements)

    @property
    def total_elements(self):
        return len(self.elements)

    def collect_v_models(self):
        duplicated = False
        dcd_vendor_model = []
        for e in self.elements:
            for me in e.vendor_models:
                for m_dcd in dcd_vendor_model:
                    if me.name == m_dcd.name or me.mid == m_dcd.mid:
                        duplicated = True
                if duplicated == False:
                    dcd_vendor_model.append(me)
                duplicated = False
        return dcd_vendor_model

    def add_chunk(self, chunk):
        for e in chunk:
            for element in self.elements:
                if element.is_mergeable_with(e):
                    element.merge_with(e)
                    break
            else:
                self.elements.append(e)

    def validate(self):
        # The code below is not running when there is only one element because
        # no cross-checks are necessary.
        # It is not allowed to have the same group name from the same file
        # on different elements because the macro value is ambiguous.
        validation_error_list = []
        for elem_idx, elem in enumerate(self.elements):
            for elem_other_idx in range(elem_idx + 1, len(self.elements)):
                elem_other = self.elements[elem_other_idx]
                for source in elem.sources:
                    if source.group is None:
                        # If group is none then group macro is not generated so
                        # duplication shall not be checked.
                        continue
                    if source in elem_other.sources:
                        # If any source group is None in other element then the
                        # equality check is evaluated to false for sure because
                        # source.group is not None due to previous check.
                        validation_error = (
                            f"BtMeshGenerator: Duplicated group ({source.group}) "
                            f"from same file ({source.filename}.dcd) on different "
                            f"elements ({elem.name}, {elem_other.name})."
                        )
                        validation_error_list.append(validation_error)
        if validation_error_list:
            print("\n".join(validation_error_list))
            exit(-1)


def dcd_chunk(filename, elements=[]):
    return [Element(**e, sources=[Source(filename, e.get("group"))]) for e in elements]


def dcdgen(dcd):
    dcd.unique_vendor_models = dcd.collect_v_models()
    env = Environment(lstrip_blocks=True, trim_blocks=True, keep_trailing_newline=True)
    env.loader = FileSystemLoader(os.path.dirname(__file__))
    return (
        env.get_template("templates/sl_btmesh_dcd.h.template").render(dcd=dcd),
        env.get_template("templates/sl_btmesh_dcd.c.template").render(dcd=dcd),
    )


if __name__ == "__main__":
    import sys
    import argparse

    cwd = os.getcwd()
    parser = argparse.ArgumentParser(
        description="BLE Mesh Device Composition Data code generator"
    )
    parser.add_argument("input", nargs="?", default=cwd)
    parser.add_argument("output", nargs="?", default=cwd)

    args = parser.parse_args()

    if os.path.isdir(args.input):
        input_filename = next(
            x for x in os.listdir(args.input) if x.lower().endswith(".btmeshconf")
        )
        if not input_filename:
            print(f"BtMeshGenerator: No 'btmeshconf' file found in {args.input}.")
            exit(1)
        dcd_files = [
            os.path.join(args.input, filename)
            for filename in os.listdir(args.input)
            if filename.lower().endswith(".dcd")
        ]
        input_path = os.path.join(args.input, input_filename)
    else:
        input_path = args.input

    with open(input_path) as f:
        btmeshconf = json.load(f)

    dcd = DCD(**btmeshconf["composition_data"])

    dcd_chunks = []
    for dcd_file in dcd_files:
        filename = os.path.basename(dcd_file)
        filename = os.path.splitext(filename)[0]
        with open(dcd_file) as f:
            dcd.add_chunk(dcd_chunk(filename, json.load(f)))
    dcd.validate()
    dcd_h_file, dcd_c_file = dcdgen(dcd)

    dcd_c_path = os.path.join(args.output, "sl_btmesh_dcd.c")
    dcd_h_path = os.path.join(args.output, "sl_btmesh_dcd.h")

    with open(dcd_h_path, "w") as f:
        f.write(dcd_h_file)
    with open(dcd_c_path, "w") as f:
        f.write(dcd_c_file)

    print(f"BtMeshGenerator: DCD written to {os.path.abspath(dcd_c_path)}")
