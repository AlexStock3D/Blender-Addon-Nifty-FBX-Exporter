import bpy
from bpy.props import *


# -----------------------------------------------------------------------------
# Schema — single source of truth for config cloning and preset I/O
# -----------------------------------------------------------------------------

# Maps each NIFTYFBX_ExportConfig sub-group name to the list of field names it
# owns.  clone_export_config() iterates this to copy values, and operators.py's
# _PRESET_SCHEMA references the same group names to map preset file attributes.
# When adding a new property to a sub-group, add its name here too.
_CONFIG_SCHEMA = {
    'source_rules': ['include_custom_props', 'embed_textures', 'object_filter'],
    'output_rules': ['world_scale', 'scale_method', 'forward_axis', 'world_up',
                     'honor_scene_units', 'bake_transforms'],
    'mesh_proc':    ['normals_mode', 'preserve_subdivision', 'apply_modifiers',
                     'export_loose_edges', 'tangent_space'],
    'rig_opts':     ['primary_axis', 'secondary_axis', 'root_node_type',
                     'deform_only', 'leaf_bones'],
    'anim_bake':    ['bake_enabled', 'key_all_bones', 'nla_strips', 'all_actions',
                     'force_frame_range', 'frame_step', 'simplify_factor'],
}


def clone_export_config(dst, src):
    """Copy all tracked field values from src to dst using _CONFIG_SCHEMA.
    Only the fields listed in the schema are touched; UI-only state such as
    is_open and active_preset must be copied separately by the caller."""
    for grp, fields in _CONFIG_SCHEMA.items():
        src_grp = getattr(src, grp)
        dst_grp = getattr(dst, grp)
        for field in fields:
            setattr(dst_grp, field, getattr(src_grp, field))


def _copy_ref_collection(dst_coll, src_coll):
    """Replace dst_coll's contents with shallow copies of every slot in
    src_coll.  Clears dst first so the result is always a clean replica."""
    dst_coll.clear()
    for item in src_coll:
        dst_coll.add().ref = item.ref


def _sanitize_global_output_dir(self, context):
    # Keep slash style consistent across platforms and Blender versions.
    clean_path = self.global_output_dir.replace("\\", "/")
    if clean_path != self.global_output_dir:
        self.global_output_dir = clean_path


# -----------------------------------------------------------------------------
# Scene reference wrappers for objects / collections
# -----------------------------------------------------------------------------

class NIFTYFBX_SceneObject(bpy.types.PropertyGroup):
    """Single scene-object slot inside an export package."""
    ref: bpy.props.PointerProperty(
        name="Object",
        type=bpy.types.Object
    )


class NIFTYFBX_SceneCollection(bpy.types.PropertyGroup):
    """Single collection slot inside an export package."""
    ref: bpy.props.PointerProperty(
        name="Collection",
        type=bpy.types.Collection
    )


# -----------------------------------------------------------------------------
# Source Rules — which scene content gets included
# -----------------------------------------------------------------------------

class NIFTYFBX_SourceRules(bpy.types.PropertyGroup):

    is_open: BoolProperty(
        name="Panel Open",
        default=True
    )

    include_custom_props: BoolProperty(
        name="Custom Properties",
        description="Carry custom object/mesh properties through to the exported FBX",
        default=True
    )

    embed_textures: BoolProperty(
        name="Embed Textures",
        description="Pack texture data directly inside the FBX file",
        default=False
    )

    object_filter: EnumProperty(
        name="Allowed Object Types",
        description="Object types that are eligible for export",
        options={'ENUM_FLAG'},
        items=[
            ('MESH',     "Mesh",     "Mesh objects",                       'MESH_DATA',     1),
            ('ARMATURE', "Armature", "Armature objects",                   'ARMATURE_DATA', 2),
            ('EMPTY',    "Empty",    "Empty objects",                      'EMPTY_DATA',    4),
            ('LIGHT',    "Light",    "Light objects",                      'LIGHT',         8),
            ('CAMERA',   "Camera",   "Camera objects",                     'CAMERA_DATA',   16),
            ('OTHER',    "Other",    "Any other type (curves, fonts, …)",  'OBJECT_DATA',   32),
        ],
        default={'MESH', 'ARMATURE', 'EMPTY', 'OTHER'},
    )

    filter_expanded: BoolProperty(
        name="Object Filter Expanded",
        default=True
    )


# -----------------------------------------------------------------------------
# Output Rules — coordinate space and scale for the exported file
# -----------------------------------------------------------------------------

class NIFTYFBX_OutputRules(bpy.types.PropertyGroup):

    is_open: BoolProperty(
        name="Panel Open",
        default=True
    )

    world_scale: FloatProperty(
        name="Scale",
        description="Uniform scale factor applied to the exported scene",
        default=1.0
    )

    scale_method: EnumProperty(
        name="Apply Scalings",
        description="How scale factors are folded into the FBX output",
        default="FBX_SCALE_NONE",
        items=[
            ("FBX_SCALE_NONE",   "All Local",       "Custom scaling and units scaling applied per-object; FBX scale stays at 1.0.", "", 0),
            ("FBX_SCALE_UNITS",  "FBX Units Scale", "Custom scaling per-object; units scaling written to the FBX scale field.",     "", 1),
            ("FBX_SCALE_CUSTOM", "FBX Custom Scale","Custom scaling in the FBX scale field; units scaling per-object.",             "", 2),
            ("FBX_SCALE_ALL",    "FBX All",         "Both custom and units scaling written to the FBX scale field.",                "", 3),
        ]
    )

    forward_axis: EnumProperty(
        name="Forward",
        default="-Z",
        items=[
            ("X",  "X Forward",  "", "", 0),
            ("Y",  "Y Forward",  "", "", 1),
            ("Z",  "Z Forward",  "", "", 2),
            ("-X", "-X Forward", "", "", 3),
            ("-Y", "-Y Forward", "", "", 4),
            ("-Z", "-Z Forward", "", "", 5),
        ]
    )

    world_up: EnumProperty(
        name="Up",
        default="Y",
        items=[
            ("X",  "X Up",  "", "", 0),
            ("Y",  "Y Up",  "", "", 1),
            ("Z",  "Z Up",  "", "", 2),
            ("-X", "-X Up", "", "", 3),
            ("-Y", "-Y Up", "", "", 4),
            ("-Z", "-Z Up", "", "", 5),
        ]
    )

    honor_scene_units: BoolProperty(
        name="Apply Unit",
        description="Apply the current Blender unit scale to the export (disable to export raw values)",
        default=True
    )

    bake_transforms: BoolProperty(
        name="Apply Transform",
        description="Fold all transformations into mesh data at export time (EXPERIMENTAL)",
        default=False
    )


# -----------------------------------------------------------------------------
# Mesh Processing — how mesh geometry is prepared for export
# -----------------------------------------------------------------------------

class NIFTYFBX_MeshProcessing(bpy.types.PropertyGroup):

    is_open: BoolProperty(
        name="Panel Open",
        default=True
    )

    normals_mode: EnumProperty(
        name="Smoothing",
        description="How normals / smoothing data is written to the FBX",
        default="OFF",
        items=[
            ("OFF",  "Normals Only", "Export vertex normals without smoothing groups", "", 0),
            ("FACE", "Face",         "Write per-face smoothing groups",                "", 1),
            ("EDGE", "Edge",         "Write edge-crease smoothing groups",             "", 2),
        ]
    )

    preserve_subdivision: BoolProperty(
        name="Export Subdivision Surface",
        description="Export the final Catmull-Clark subdivison modifier as FBX subdivision data instead of baking it",
        default=False
    )

    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Evaluate and apply all mesh modifiers before export",
        default=True
    )

    export_loose_edges: BoolProperty(
        name="Loose Edges",
        description="Write lone edges as degenerate two-vertex polygons",
        default=False
    )

    tangent_space: BoolProperty(
        name="Tangent Space",
        description="Compute and export binormal and tangent vectors (meshes must be all-tris or all-quads)",
        default=False
    )


# -----------------------------------------------------------------------------
# Rig Options — armature and bone export settings
# -----------------------------------------------------------------------------

class NIFTYFBX_RigOptions(bpy.types.PropertyGroup):

    is_open: BoolProperty(
        name="Panel Open",
        default=True
    )

    primary_axis: EnumProperty(
        name="Primary Bone Axis",
        default="Y",
        items=[
            ("X",  "X Axis",  "", "", 0),
            ("Y",  "Y Axis",  "", "", 1),
            ("Z",  "Z Axis",  "", "", 2),
            ("-X", "-X Axis", "", "", 3),
            ("-Y", "-Y Axis", "", "", 4),
            ("-Z", "-Z Axis", "", "", 5),
        ]
    )

    secondary_axis: EnumProperty(
        name="Secondary Bone Axis",
        default="X",
        items=[
            ("X",  "X Axis",  "", "", 0),
            ("Y",  "Y Axis",  "", "", 1),
            ("Z",  "Z Axis",  "", "", 2),
            ("-X", "-X Axis", "", "", 3),
            ("-Y", "-Y Axis", "", "", 4),
            ("-Z", "-Z Axis", "", "", 5),
        ]
    )

    root_node_type: EnumProperty(
        name="Armature FBXNode Type",
        description="FBX node type used to represent the armature root",
        default="NULL",
        items=[
            ("NULL",     "Null",      "", "", 0),
            ("ROOT",     "Root",      "", "", 1),
            ("LIMBNODE", "Limb Node", "", "", 2),
        ]
    )

    deform_only: BoolProperty(
        name="Only Deform Bones",
        description="Skip non-deforming bones unless they are ancestors of deforming ones",
        default=False
    )

    leaf_bones: BoolProperty(
        name="Add Leaf Bones",
        description="Append a terminal leaf bone to each chain to preserve chain-end length",
        default=True
    )


# -----------------------------------------------------------------------------
# Animation Bake — keyframe sampling and NLA/action export
# -----------------------------------------------------------------------------

class NIFTYFBX_AnimBake(bpy.types.PropertyGroup):

    is_open: BoolProperty(
        name="Panel Open",
        default=True
    )

    bake_enabled: BoolProperty(
        name="Baked Animation",
        description="Sample and bake animation curves for export",
        default=True
    )

    key_all_bones: BoolProperty(
        name="Key All Bones",
        description="Insert at least one keyframe per bone (required by some engines such as Unreal)",
        default=True
    )

    nla_strips: BoolProperty(
        name="NLA Strips",
        description="Export each non-muted NLA strip as a separate FBX AnimStack",
        default=True
    )

    all_actions: BoolProperty(
        name="All Actions",
        description="Export every action in the scene as a separate FBX AnimStack",
        default=True
    )

    force_frame_range: BoolProperty(
        name="Force Start/End Keying",
        description="Always write keyframes at the very first and last frame of each action",
        default=True
    )

    frame_step: FloatProperty(
        name="Sampling Rate",
        description="Interval between evaluated frames during animation bake (1 = every frame)",
        default=1.0,
        min=0.01,
    )

    simplify_factor: FloatProperty(
        name="Simplify",
        description="Keyframe reduction aggressiveness (0 = off, higher = fewer keys)",
        default=1.0,
        min=0.0,
    )


# -----------------------------------------------------------------------------
# Aggregated Export Configuration
# -----------------------------------------------------------------------------

class NIFTYFBX_ExportConfig(bpy.types.PropertyGroup):
    """All FBX export settings for one package (or the global override)."""

    is_open: BoolProperty(
        name="Panel Open",
        default=False
    )

    active_preset: StringProperty(
        name="Active Preset",
        default=""
    )

    source_rules: PointerProperty(
        name="Source Rules",
        type=NIFTYFBX_SourceRules
    )

    output_rules: PointerProperty(
        name="Output Rules",
        type=NIFTYFBX_OutputRules
    )

    mesh_proc: PointerProperty(
        name="Mesh Processing",
        type=NIFTYFBX_MeshProcessing
    )

    rig_opts: PointerProperty(
        name="Rig Options",
        type=NIFTYFBX_RigOptions
    )

    anim_bake: PointerProperty(
        name="Animation Bake",
        type=NIFTYFBX_AnimBake
    )


# -----------------------------------------------------------------------------
# Export Job — the core unit of work
# -----------------------------------------------------------------------------
# Each job describes one discrete FBX export: which scene content to include
# (sources), where to write the file (destination), how the FBX should be
# configured (runtime options), and an optional temporary transform override
# applied during export and rolled back afterwards.

class NIFTYFBX_ExportJob(bpy.types.PropertyGroup):

    # ── Identity / naming ────────────────────────────────────────────
    is_open: BoolProperty(
        name="Job Expanded",
        default=True
    )

    is_active: BoolProperty(
        name="Active",
        description="Include this job in Export All (active mode) and in auto-export on save (active mode)",
        default=True
    )

    name: StringProperty(
        name="Name",
        default="Untitled Export Package"
    )

    # ── Destination ─────────────────────────────────────────────────
    def _sanitize_output_dir(self, context):
        # Blender's DIR_PATH subtype can write back-slashes on Windows.
        # Normalise to forward-slashes so paths work cross-platform.
        clean_path = self.output_dir.replace("\\", "/")
        if clean_path != self.output_dir:
            self.output_dir = clean_path

    output_dir: StringProperty(
        name="Output",
        default="NiftyFBXExport/",
        update=_sanitize_output_dir,
    )

    # ── Sources — scene content that feeds this job ──────────────────
    sources_expanded: BoolProperty(
        name="Sources Panel Visible",
        default=True
    )

    collections: CollectionProperty(
        name="Source Collections",
        type=NIFTYFBX_SceneCollection
    )

    objects: CollectionProperty(
        name="Source Objects",
        type=NIFTYFBX_SceneObject
    )

    selected_source_idx: IntProperty(
        name="Selected Source Index",
        description="Currently highlighted item in the sources list",
        default=0
    )

    # ── Runtime Options — FBX export configuration ──────────────────
    export_cfg: PointerProperty(
        name="Export Configuration",
        type=NIFTYFBX_ExportConfig
    )

    # ── Temporary Transform Override ────────────────────────────────
    # Moves a designated object to a chosen world position for the duration
    # of the export, then restores it.  Useful for exporting at origin.
    transform_enabled: BoolProperty(
        name="Enable Relocation",
        description="Temporarily reposition an object during export then restore original location",
        default=False
    )

    transform_expanded: BoolProperty(
        name="Transform Panel Visible",
        default=False
    )

    transform_object: PointerProperty(
        name="Transform Object",
        description="Object to temporarily move during export (children follow)",
        type=bpy.types.Object
    )

    transform_origin: FloatVectorProperty(
        name="Export Origin",
        description="World-space position the object is moved to during export",
        subtype='TRANSLATION',
        size=3,
        default=(0.0, 0.0, 0.0)
    )


def clone_job(dst, src):
    """Full deep-copy of src into dst for the duplicate operator.
    Copies all scalar fields, then rebuilds both source collections, then
    delegates export_cfg cloning to clone_export_config()."""
    dst.name                = src.name
    dst.output_dir          = src.output_dir
    dst.is_active           = src.is_active
    dst.transform_enabled   = src.transform_enabled
    dst.transform_expanded  = src.transform_expanded
    dst.transform_object    = src.transform_object
    dst.transform_origin    = src.transform_origin
    dst.selected_source_idx = src.selected_source_idx

    _copy_ref_collection(dst.collections, src.collections)
    _copy_ref_collection(dst.objects,     src.objects)

    # Copy config panel-state separately (not part of _CONFIG_SCHEMA) then
    # delegate the actual settings values to the schema-driven function.
    dst.export_cfg.is_open       = src.export_cfg.is_open
    dst.export_cfg.active_preset = src.export_cfg.active_preset
    clone_export_config(dst.export_cfg, src.export_cfg)


# -----------------------------------------------------------------------------
# Root Addon Data Container
# -----------------------------------------------------------------------------
# One instance of this is stored on every Blender scene via
# bpy.types.Scene.nifty_fbx_exporter.  It holds the package list plus the
# global overrides that apply across all packages when enabled.

class NIFTYFBX_AddonState(bpy.types.PropertyGroup):
    jobs: CollectionProperty(
        name="Export Jobs",
        type=NIFTYFBX_ExportJob
    )

    active_job_idx: IntProperty(
        name="Active Job Index",
        default=0
    )

    auto_export_mode: EnumProperty(
        name="Auto-Export Mode",
        description="Which jobs are exported automatically when the .blend file is saved",
        default="DISABLED",
        items=[
            ("DISABLED", "Disabled",     "No automatic export on save",                      "", 0),
            ("ALL",      "All Jobs",     "Export every job in the list on save",              "", 1),
            ("ACTIVE",   "Active Jobs",  "Export only jobs whose Active checkbox is enabled", "", 2),
        ]
    )

    global_opts_visible: BoolProperty(
        name="Global Options Visible",
        default=False
    )

    output_prefix: StringProperty(
        name="Filename Prefix",
        description="Text prepended to the package name in the output filename",
        default=""
    )

    output_suffix: StringProperty(
        name="Filename Suffix",
        description="Text appended to the package name in the output filename",
        default=""
    )

    use_global_config: BoolProperty(
        name="Use Global Export Config",
        description="Apply a single export configuration to all packages",
        default=False
    )

    use_global_output_dir: BoolProperty(
        name="Use Global Output Directory",
        description="Use a single output directory for all packages",
        default=False
    )

    global_output_dir: StringProperty(
        name="Global Output Directory",
        description="Shared output directory used when the global path toggle is on",
        default="NiftyFBXExport/",
        update=_sanitize_global_output_dir,
    )

    global_export_config: PointerProperty(
        name="Global Export Configuration",
        type=NIFTYFBX_ExportConfig
    )

    show_active_checkboxes: BoolProperty(
        name="Show Active Checkboxes",
        description="Show per-package active checkboxes; Export All only runs checked packages",
        default=False
    )


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------
# Classes must be registered in dependency order: leaf types first,
# then the groups that reference them, and the root container last.

_classes = (
    NIFTYFBX_SceneObject,
    NIFTYFBX_SceneCollection,
    NIFTYFBX_SourceRules,
    NIFTYFBX_OutputRules,
    NIFTYFBX_MeshProcessing,
    NIFTYFBX_RigOptions,
    NIFTYFBX_AnimBake,
    NIFTYFBX_ExportConfig,
    NIFTYFBX_ExportJob,
    NIFTYFBX_AddonState,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.nifty_fbx_exporter = bpy.props.PointerProperty(type=NIFTYFBX_AddonState)


def unregister():
    del bpy.types.Scene.nifty_fbx_exporter
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

