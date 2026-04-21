import bpy
import os
import glob
import traceback


# ─── Viewport / scene helpers ─────────────────────────────────────────────────

def show_error_popup(message, title="Nifty FBX Exporter"):
	"""Display an error as a popup dialog box in Blender."""
	def draw(self, context):
		for line in message.split("\n"):
			self.layout.label(text=line)
	bpy.context.window_manager.popup_menu(draw, title=title, icon='ERROR')


def find_layer_collection(layer_col, collection):
	"""Recursively walk the layer-collection tree and return the node whose
	.collection matches the given Collection, or None if not found."""
	if layer_col.collection == collection:
		return layer_col
	for child in layer_col.children:
		result = find_layer_collection(child, collection)
		if result:
			return result
	return None


def ensure_job_visible(context, job):
	"""Temporarily make all objects in a job reachable for selection/export.
	Unhides objects, un-excludes layer collections, and resets viewport visibility.
	Called before select and isolate operations — NOT before export (export does
	its own targeted unhiding so it can restore state precisely afterwards)."""
	for col_slot in job.collections:
		if col_slot.ref is None:
			continue
		lc = find_layer_collection(context.view_layer.layer_collection, col_slot.ref)
		if lc and lc.exclude:
			lc.exclude = False

	for col_slot in job.collections:
		if col_slot.ref is None:
			continue
		for obj in col_slot.ref.all_objects:
			if obj.hide_get():
				obj.hide_set(False)
			if obj.hide_viewport:
				obj.hide_viewport = False

	for obj_slot in job.objects:
		if obj_slot.ref is None:
			continue
		if obj_slot.ref.hide_get():
			obj_slot.ref.hide_set(False)
		if obj_slot.ref.hide_viewport:
			obj_slot.ref.hide_viewport = False



# ─── Preset pipeline ─────────────────────────────────────────────────────────
# Presets are stored as plain Python files in Blender's standard preset location
# (scripts/presets/operator/export_scene.fbx/).  Each file contains lines of the
# form "op.attribute = value", which is the format Blender itself writes.
# Lines are parsed with ast.literal_eval so no arbitrary code is executed.

def get_fbx_preset_paths():
	"""Return a sorted list of (filepath, display_name) tuples for every
	FBX export preset found across all Blender preset search paths."""
	presets = []
	preset_dirs = bpy.utils.preset_paths('operator/export_scene.fbx/')
	for d in preset_dirs:
		if not os.path.isdir(d):
			continue
		for f in sorted(glob.glob(os.path.join(d, '*.py'))):
			name = os.path.splitext(os.path.basename(f))[0]
			presets.append((f, name))
	return presets


def get_fbx_preset_enum_items(self, context):
	"""Dynamic enum callback — prepends a sentinel 'No Preset' entry so operators
	can detect when the user wants to reset settings rather than apply one."""
	items = [("NONE", "— No Preset —", "Keep current settings", 0)]
	for i, (filepath, name) in enumerate(get_fbx_preset_paths()):
		items.append((filepath, name, "Apply preset: " + name, i + 1))
	return items


# ─── Preset adapter ───────────────────────────────────────────────────────────
# Blender preset files use the exporter operator's own attribute names.  Those
# names are an external format — a compatibility surface.  The adapter table
# below is the single boundary that translates them into the internal config
# model.  Internal field names are independent of Blender's naming and are not
# required to match Blender's attribute names in any way.

_PRESET_ADAPTER = {
	# ── Source Rules ──────────────────────────────────────────────────────────
	'use_custom_props':                ('source_rules', 'include_custom_props'),
	# ── Output Rules ─────────────────────────────────────────────────────────
	'global_scale':                    ('output_rules', 'world_scale'),
	'apply_scale_options':             ('output_rules', 'scale_method'),
	'axis_forward':                    ('output_rules', 'forward_axis'),
	'axis_up':                         ('output_rules', 'world_up'),
	'apply_unit_scale':                ('output_rules', 'honor_scene_units'),
	'bake_space_transform':            ('output_rules', 'bake_transforms'),
	# ── Mesh Processing ───────────────────────────────────────────────────────
	'mesh_smooth_type':                ('mesh_proc', 'normals_mode'),
	'use_subsurf':                     ('mesh_proc', 'preserve_subdivision'),
	'use_mesh_modifiers':              ('mesh_proc', 'apply_modifiers'),
	'use_mesh_edges':                  ('mesh_proc', 'export_loose_edges'),
	'use_tspace':                      ('mesh_proc', 'tangent_space'),
	# ── Rig Options ───────────────────────────────────────────────────────────
	'primary_bone_axis':               ('rig_opts', 'primary_axis'),
	'secondary_bone_axis':             ('rig_opts', 'secondary_axis'),
	'armature_nodetype':               ('rig_opts', 'root_node_type'),
	'use_armature_deform_only':        ('rig_opts', 'deform_only'),
	'add_leaf_bones':                  ('rig_opts', 'leaf_bones'),
	# ── Animation Bake ────────────────────────────────────────────────────────
	'bake_anim':                       ('anim_bake', 'bake_enabled'),
	'bake_anim_use_all_bones':         ('anim_bake', 'key_all_bones'),
	'bake_anim_use_nla_strips':        ('anim_bake', 'nla_strips'),
	'bake_anim_use_all_actions':       ('anim_bake', 'all_actions'),
	'bake_anim_force_startend_keying': ('anim_bake', 'force_frame_range'),
	'bake_anim_step':                  ('anim_bake', 'frame_step'),
	'bake_anim_simplify_factor':       ('anim_bake', 'simplify_factor'),
}


def _parse_preset_file(filepath):
	"""Stage 1 — parse.  Read a Blender-format preset file and return a plain
	{blender_attr: value} dict.  This function is format-only; it knows nothing
	about the config model and only understands the on-disk format ("op.attr = value").
	Uses ast.literal_eval so no arbitrary code is executed."""
	import ast
	import re
	raw = {}
	with open(filepath, 'r') as fh:
		for line in fh:
			m = re.match(r'^op\.(\w+)\s*=\s*(.+)$', line.strip())
			if not m:
				continue
			try:
				raw[m.group(1)] = ast.literal_eval(m.group(2))
			except (ValueError, SyntaxError):
				pass
	return raw


def _adapt_to_internal(raw_values):
	"""Stage 2 — adapt.  Convert {blender_attr: value} into a list of
	(group, field, value) triples using the adapter table.
	Blender attributes without an adapter entry are discarded; these correspond
	to exporter options that are intentionally not exposed by this addon."""
	adapted = []
	for blender_attr, value in raw_values.items():
		entry = _PRESET_ADAPTER.get(blender_attr)
		if entry is not None:
			group, field = entry
			adapted.append((group, field, value))
	return adapted


def _write_to_config(adapted, cfg):
	"""Stage 3 — write.  Apply a list of (group, field, value) triples to a
	NIFTYFBX_ExportConfig, targeting the normalized property groups.
	Returns the number of values successfully written."""
	written = 0
	for group, field, value in adapted:
		grp = getattr(cfg, group, None)
		if grp is None:
			continue
		try:
			setattr(grp, field, value)
			written += 1
		except Exception:
			pass
	return written


def _read_from_config(cfg):
	"""Reverse direction — read.  Walk the adapter table backwards to collect
	current internal values from a NIFTYFBX_ExportConfig and return a
	{blender_attr: value} dict ready for serialisation."""
	out = {}
	for blender_attr, (group, field) in _PRESET_ADAPTER.items():
		grp = getattr(cfg, group, None)
		if grp is not None:
			out[blender_attr] = getattr(grp, field)
	return out


def get_user_preset_dir():
	"""Return the user FBX export preset directory, creating it if needed.
	Only presets in this directory can be saved or removed by the addon;
	presets shipped with Blender are treated as read-only."""
	base = bpy.utils.user_resource('SCRIPTS')
	dir_path = os.path.join(base, 'presets', 'operator', 'export_scene.fbx')
	os.makedirs(dir_path, exist_ok=True)
	return dir_path


def is_user_preset(filepath):
	"""Return True when filepath lives inside the user preset directory.
	Used as a safety guard to prevent removing Blender's built-in presets."""
	user_dir = get_user_preset_dir()
	return os.path.normpath(filepath).startswith(os.path.normpath(user_dir))


def save_fbx_preset(name, cfg):
	"""Serialise a NIFTYFBX_ExportConfig as a standard Blender FBX preset file.
	Reads internal values via _read_from_config, then writes them in Blender's
	"op.attr = value" format so the file is usable outside this addon."""
	values     = _read_from_config(cfg)
	preset_dir = get_user_preset_dir()
	filepath   = os.path.join(preset_dir, name.strip() + ".py")
	lines = ["import bpy", "op = bpy.context.active_operator", ""]
	for blender_attr, value in values.items():
		lines.append("op." + blender_attr + " = " + repr(value))
	with open(filepath, 'w') as fh:
		fh.write("\n".join(lines) + "\n")
	return filepath


def get_removable_preset_enum_items(self, context):
	"""Dynamic enum items callback for removable (user) FBX presets."""
	items = []
	user_dir = get_user_preset_dir()
	for i, (filepath, name) in enumerate(get_fbx_preset_paths()):
		if os.path.normpath(filepath).startswith(os.path.normpath(user_dir)):
			items.append((filepath, name, "Remove preset: " + name, i))
	if not items:
		items.append(("NONE", "No User Presets", "No user presets to remove", 0))
	return items


def apply_fbx_preset(filepath, cfg):
	"""Load a Blender FBX preset file and write its values into a
	NIFTYFBX_ExportConfig.  Three-stage pipeline:
	  1. _parse_preset_file  — read raw {blender_attr: value} from disk
	  2. _adapt_to_internal  — convert to [(group, field, value)] via the adapter
	  3. _write_to_config    — apply normalised values to the property groups
	Returns the number of settings successfully written."""
	raw     = _parse_preset_file(filepath)
	adapted = _adapt_to_internal(raw)
	return _write_to_config(adapted, cfg)



# ─── Operator utility decorator ───────────────────────────────────────────────

def safe_execute(func):
	"""Decorator for operator execute() methods.  Wraps the body in a try/except
	so any unhandled exception surfaces as a Blender popup instead of silently
	failing; the full traceback is always printed to the system console."""
	def wrapper(self, context):
		try:
			return func(self, context)
		except Exception as e:
			traceback.print_exc()
			show_error_popup(str(e))
			return {'CANCELLED'}
	return wrapper


# ─── Preset operators ─────────────────────────────────────────────────────────

class NIFTYFBX_OT_apply_fbx_preset(bpy.types.Operator):
	"""Apply a Blender FBX export preset to the selected package"""
	bl_idname = "nifty_fbx_exporter.apply_fbx_preset"
	bl_label = "Apply FBX Preset"
	bl_options = {'REGISTER', 'UNDO'}

	preset: bpy.props.EnumProperty(
		name="Preset",
		description="Select an FBX export preset to apply",
		items=get_fbx_preset_enum_items,
	)

	@safe_execute
	def execute(self, context):
		data = context.scene.nifty_fbx_exporter

		if data.use_global_config:
			settings = data.global_export_config
		else:
			if data.active_job_idx < 0 or data.active_job_idx >= len(data.jobs):
				raise RuntimeError("No valid job selected.")
			settings = data.jobs[data.active_job_idx].export_cfg

		if self.preset == "NONE":
			# Reset all sub-groups to their property defaults
			for grp_name in ('source_rules', 'output_rules', 'mesh_proc', 'rig_opts', 'anim_bake'):
				grp = getattr(settings, grp_name)
				for prop in grp.bl_rna.properties:
					if prop.identifier in ('rna_type', 'name', 'is_open'):
						continue
					if not prop.is_readonly:
						grp.property_unset(prop.identifier)
			settings.active_preset = ""
			self.report({'INFO'}, "Nifty FBX Exporter: Reset to default settings")
			return {'FINISHED'}

		preset_name = os.path.splitext(os.path.basename(self.preset))[0]
		count = apply_fbx_preset(self.preset, settings)
		settings.active_preset = preset_name
		self.report({'INFO'}, "Nifty FBX Exporter: Applied preset '" + preset_name + "' (" + str(count) + " settings)")
		return {'FINISHED'}


class NIFTYFBX_OT_save_fbx_preset(bpy.types.Operator):
	"""Save the selected package's FBX settings as a reusable preset"""
	bl_idname = "nifty_fbx_exporter.save_fbx_preset"
	bl_label = "Save FBX Preset"
	bl_options = {'REGISTER', 'UNDO'}

	name: bpy.props.StringProperty(
		name="Preset Name",
		description="Name for the new preset",
		default="My Preset",
	)

	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self)

	@safe_execute
	def execute(self, context):
		data = context.scene.nifty_fbx_exporter

		if data.use_global_config:
			settings = data.global_export_config
		else:
			if data.active_job_idx < 0 or data.active_job_idx >= len(data.jobs):
				raise RuntimeError("No valid job selected.")
			settings = data.jobs[data.active_job_idx].export_cfg

		if not self.name.strip():
			raise RuntimeError("Preset name cannot be empty.")

		save_fbx_preset(self.name, settings)
		self.report({'INFO'}, "Nifty FBX Exporter: Saved preset '" + self.name.strip() + "'")
		return {'FINISHED'}


class NIFTYFBX_OT_remove_fbx_preset(bpy.types.Operator):
	"""Remove a user-created FBX export preset"""
	bl_idname = "nifty_fbx_exporter.remove_fbx_preset"
	bl_label = "Remove FBX Preset"
	bl_options = {'REGISTER', 'UNDO'}

	preset: bpy.props.EnumProperty(
		name="Preset",
		description="Select a user preset to remove",
		items=get_removable_preset_enum_items,
	)

	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self)

	@safe_execute
	def execute(self, context):
		if self.preset == "NONE":
			self.report({'WARNING'}, "Nifty FBX Exporter: No user presets to remove")
			return {'CANCELLED'}

		if not is_user_preset(self.preset):
			raise RuntimeError("Cannot remove system presets.")

		name = os.path.splitext(os.path.basename(self.preset))[0]
		os.remove(self.preset)

		# Clear active_preset on any config that still references the deleted preset
		addon_data = context.scene.nifty_fbx_exporter
		if addon_data.global_export_config.active_preset == name:
			addon_data.global_export_config.active_preset = ""
		for job in addon_data.jobs:
			if job.export_cfg.active_preset == name:
				job.export_cfg.active_preset = ""

		self.report({'INFO'}, "Nifty FBX Exporter: Removed preset '" + name + "'")
		return {'FINISHED'}



# ─── Export pipeline ──────────────────────────────────────────────────────────
# The export flow is split into focused helpers so each responsibility is
# independently readable and testable.  run_export_job() composes them in order;
# both export operators are thin coordinators that delegate to it.


class _SceneSnapshot:
	"""Captures viewport and selection state before any scene modification so
	it can be restored exactly afterwards, even if an exception is raised."""

	def __init__(self, context):
		self.selected = list(context.selected_objects)
		self.active   = context.active_object
		self.mode     = context.object.mode if context.object else 'OBJECT'

	def restore(self, context):
		"""Return selection, active object, and interaction mode to their captured state."""
		if context.view_layer.objects.active and context.view_layer.objects.active.mode != 'OBJECT':
			bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
		bpy.ops.object.select_all(action='DESELECT')
		context.view_layer.objects.active = self.active
		for obj in self.selected:
			try:
				obj.select_set(True)
			except Exception:
				pass  # object may have been deleted between snapshot and restore
		if context.view_layer.objects.active and self.mode != 'OBJECT':
			bpy.ops.object.mode_set(mode=self.mode, toggle=False)


class _VisibilityRestore:
	"""Tracks every object and layer collection that was temporarily exposed
	during export preparation so they can all be re-hidden in a single call."""

	def __init__(self):
		self._hidden_objs   = []  # will call hide_set(True)
		self._viewport_objs = []  # will set hide_viewport = True
		self._excluded_cols = []  # will set lc.exclude = True

	def expose_object(self, obj):
		"""Unhide obj in the scene and record it so restore() can re-hide it."""
		if obj.hide_get():
			obj.hide_set(False)
			self._hidden_objs.append(obj)
		if obj.hide_viewport:
			obj.hide_viewport = False
			self._viewport_objs.append(obj)

	def expose_layer_collection(self, lc):
		"""Un-exclude a layer collection and record it for restore()."""
		if lc.exclude:
			lc.exclude = False
			self._excluded_cols.append(lc)

	def restore(self):
		"""Re-hide everything that was exposed."""
		for obj in self._hidden_objs:
			obj.hide_set(True)
		for obj in self._viewport_objs:
			obj.hide_viewport = True
		for lc in self._excluded_cols:
			lc.exclude = True


def _collect_targets(job):
	"""Return the raw set of objects from all of the job's collections and
	direct object slots.  Does not apply any type filtering."""
	targets = set()
	for col_slot in job.collections:
		if col_slot.ref is not None:
			targets.update(col_slot.ref.all_objects)
	for obj_slot in job.objects:
		if obj_slot.ref is not None:
			targets.add(obj_slot.ref)
	return targets


# Blender types that are explicitly named in the object_filter enum.
# Every other type is represented by the 'OTHER' catch-all flag.
_NAMED_OBJECT_TYPES = {'MESH', 'ARMATURE', 'EMPTY', 'LIGHT', 'CAMERA'}


def _apply_type_filter(targets, object_filter):
	"""Return the subset of targets whose Blender type is permitted by object_filter."""
	result = set()
	for obj in targets:
		if obj.type in _NAMED_OBJECT_TYPES:
			if obj.type in object_filter:
				result.add(obj)
		elif 'OTHER' in object_filter:
			result.add(obj)
	return result


def _prepare_scene(job, targets, context):
	"""Un-exclude layer collections and unhide every target object.
	Returns a populated _VisibilityRestore so all changes can be undone."""
	vis = _VisibilityRestore()
	for col_slot in job.collections:
		if col_slot.ref is None:
			continue
		lc = find_layer_collection(context.view_layer.layer_collection, col_slot.ref)
		if lc is not None:
			vis.expose_layer_collection(lc)
	for obj in targets:
		vis.expose_object(obj)
	return vis


def _select_targets(targets, context):
	"""Switch to Object mode and select exactly the given targets.
	export_scene.fbx requires use_selection=True, so selection must be precise."""
	if context.view_layer.objects.active and context.view_layer.objects.active.mode != 'OBJECT':
		bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
	bpy.ops.object.select_all(action='DESELECT')
	for obj in targets:
		obj.select_set(True)


def _resolve_output_path(job, addon_data):
	"""Compute the absolute .fbx output path for this job.
	Respects global output dir and filename prefix/suffix settings."""
	out_dir = addon_data.global_output_dir if addon_data.use_global_output_dir else job.output_dir
	if not os.path.isabs(out_dir):
		blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
		out_dir = os.path.join(blend_dir, out_dir)
	if out_dir.lower().endswith('.fbx'):
		return out_dir
	filename = addon_data.output_prefix + job.job_name + addon_data.output_suffix + '.fbx'
	return os.path.join(out_dir, filename)


def _apply_transform_override(job, view_layer):
	"""Temporarily move transform_object to transform_origin.
	Returns (object, original_location) so the caller can undo the move,
	or (None, None) when the transform override is inactive."""
	if not job.transform_enabled or job.transform_object is None:
		return None, None
	obj = job.transform_object
	original = obj.location.copy()
	obj.location = job.transform_origin.copy()
	view_layer.update()
	return obj, original


def _restore_transform(obj, original_location):
	"""Return obj to its pre-export position."""
	if obj is not None and original_location is not None:
		obj.location = original_location


def _call_fbx_exporter(out_path, fbx_cfg):
	"""Invoke Blender's built-in FBX exporter with all settings from fbx_cfg."""
	bpy.ops.export_scene.fbx(
		filepath              = out_path,
		check_existing        = False,
		use_selection         = True,
		use_active_collection = False,
		object_types          = fbx_cfg.source_rules.object_filter,

		use_custom_props      = fbx_cfg.source_rules.include_custom_props,
		embed_textures        = fbx_cfg.source_rules.embed_textures,

		global_scale          = fbx_cfg.output_rules.world_scale,
		apply_scale_options   = fbx_cfg.output_rules.scale_method,
		axis_forward          = fbx_cfg.output_rules.forward_axis,
		axis_up               = fbx_cfg.output_rules.world_up,
		apply_unit_scale      = fbx_cfg.output_rules.honor_scene_units,
		bake_space_transform  = fbx_cfg.output_rules.bake_transforms,

		mesh_smooth_type      = fbx_cfg.mesh_proc.normals_mode,
		use_subsurf           = fbx_cfg.mesh_proc.preserve_subdivision,
		use_mesh_modifiers    = fbx_cfg.mesh_proc.apply_modifiers,
		use_mesh_edges        = fbx_cfg.mesh_proc.export_loose_edges,
		use_tspace            = fbx_cfg.mesh_proc.tangent_space,

		primary_bone_axis         = fbx_cfg.rig_opts.primary_axis,
		secondary_bone_axis       = fbx_cfg.rig_opts.secondary_axis,
		armature_nodetype         = fbx_cfg.rig_opts.root_node_type,
		use_armature_deform_only  = fbx_cfg.rig_opts.deform_only,
		add_leaf_bones            = fbx_cfg.rig_opts.leaf_bones,

		bake_anim                       = fbx_cfg.anim_bake.bake_enabled,
		bake_anim_use_all_bones         = fbx_cfg.anim_bake.key_all_bones,
		bake_anim_use_nla_strips        = fbx_cfg.anim_bake.nla_strips,
		bake_anim_use_all_actions       = fbx_cfg.anim_bake.all_actions,
		bake_anim_force_startend_keying = fbx_cfg.anim_bake.force_frame_range,
		bake_anim_step                  = fbx_cfg.anim_bake.frame_step,
		bake_anim_simplify_factor       = fbx_cfg.anim_bake.simplify_factor,
	)


def _log_export_contents(job):
	"""Print the collections and objects that will be included in the export."""
	for col_slot in job.collections:
		if col_slot.ref is not None:
			print("[Nifty FBX Exporter]   collection '{}'".format(col_slot.ref.name))
			for obj in col_slot.ref.all_objects:
				print("[Nifty FBX Exporter]     {}".format(obj.name))
	for obj_slot in job.objects:
		if obj_slot.ref is not None:
			print("[Nifty FBX Exporter]   object '{}'".format(obj_slot.ref.name))


def run_export_job(context, addon_data, job_index):
	"""Execute a single export job end-to-end and return the output path.

	This function is the single entry point used by both export operators.
	It resolves targets, prepares scene state, runs the FBX export, then
	restores everything — in that order, regardless of success or failure.
	Raises on any error so callers can report failures individually.
	"""
	jobs = addon_data.jobs
	if job_index < 0 or job_index >= len(jobs):
		raise IndexError("Job index {} is out of range (total: {})".format(job_index, len(jobs)))

	job     = jobs[job_index]
	fbx_cfg = addon_data.global_export_config if addon_data.use_global_config else job.export_cfg

	if not bpy.data.filepath:
		raise RuntimeError("The .blend file has not been saved. Save before exporting.")

	print("\n[Nifty FBX Exporter] Job #{} — '{}'".format(job_index, job.job_name))

	# ── 1. Collect targets (read-only; failures here need no cleanup) ────────
	raw_targets = _collect_targets(job)
	if not raw_targets:
		raise RuntimeError("Job '{}': no source objects are assigned.".format(job.job_name))

	targets = _apply_type_filter(raw_targets, fbx_cfg.source_rules.object_filter)
	if not targets:
		raise RuntimeError(
			"Job '{}': no objects survive the type filter. "
			"Check 'Allowed Object Types' in Source Rules.".format(job.job_name)
		)

	# ── 2. Snapshot + prepare scene state ────────────────────────────────────
	# Take the snapshot before any scene modification so restore() is complete.
	snapshot = _SceneSnapshot(context)
	vis      = _prepare_scene(job, targets, context)

	transform_obj      = None
	transform_original = None

	try:
		# ── 3. Select exactly the targets ────────────────────────────────────
		_select_targets(targets, context)
		if not context.selected_objects:
			raise RuntimeError("Job '{}': selection is empty after preparation.".format(job.job_name))

		_log_export_contents(job)

		# ── 4. Compute output path ────────────────────────────────────────────
		out_path = _resolve_output_path(job, addon_data)
		os.makedirs(os.path.dirname(out_path), exist_ok=True)
		print("[Nifty FBX Exporter] Output → {}".format(out_path))

		# ── 5. Apply optional transform override ──────────────────────────────
		transform_obj, transform_original = _apply_transform_override(job, context.view_layer)

		# ── 6. Run FBX export ─────────────────────────────────────────────────
		_call_fbx_exporter(out_path, fbx_cfg)
		print("[Nifty FBX Exporter] Done.")

	finally:
		# ── 7. Restore scene state (always, even on exception) ────────────────
		_restore_transform(transform_obj, transform_original)
		snapshot.restore(context)
		vis.restore()

	return out_path


# ─── Export operators ─────────────────────────────────────────────────────────

class NIFTYFBX_OT_export_all(bpy.types.Operator):
	"""Export all active jobs to their configured paths"""
	bl_idname = "nifty_fbx_exporter.export_all"
	bl_label = "Export All"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		jobs       = addon_data.jobs

		if not jobs:
			self.report({'WARNING'}, "Nifty FBX Exporter: No jobs to export")
			return {'CANCELLED'}

		filter_active = addon_data.show_active_checkboxes
		queue = [i for i, job in enumerate(jobs) if not filter_active or job.is_active]

		if not queue:
			self.report({'WARNING'}, "Nifty FBX Exporter: No active jobs to export")
			return {'CANCELLED'}

		# Exit local view for the whole batch so all objects are accessible.
		# Re-enter it afterwards to leave the viewport unchanged.
		in_local_view = bool(context.space_data and context.space_data.local_view)
		if in_local_view:
			bpy.ops.view3d.localview()

		print("\nNifty FBX Exporter: Running {} job(s)".format(len(queue)))

		succeeded = {}  # job index → output path
		failed    = {}  # job index → error message

		for i in queue:
			try:
				out_path = run_export_job(context, addon_data, i)
				succeeded[i] = out_path
			except Exception as exc:
				traceback.print_exc()
				failed[i] = str(exc)

		if in_local_view:
			bpy.ops.view3d.localview()

		if failed:
			lines = [jobs[i].job_name + ": " + msg for i, msg in failed.items()]
			self.report({'ERROR'}, "Nifty FBX Exporter: Failed jobs:\n" + "\n".join(lines))
			return {'CANCELLED'} if not succeeded else {'FINISHED'}

		self.report({'INFO'}, "Nifty FBX Exporter: Exported {} job(s)".format(len(succeeded)))
		return {'FINISHED'}


class NIFTYFBX_OT_export_single(bpy.types.Operator):
	"""Export the selected job to its configured path"""
	bl_idname = "nifty_fbx_exporter.export_single"
	bl_label = "Export Single"
	bl_options = {'REGISTER', 'UNDO'}

	index: bpy.props.IntProperty(name="Index", default=0)

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter

		# Exit local view so all objects are reachable during the export.
		in_local_view = bool(context.space_data and context.space_data.local_view)
		if in_local_view:
			bpy.ops.view3d.localview()

		try:
			out_path = run_export_job(context, addon_data, self.index)
		finally:
			if in_local_view:
				bpy.ops.view3d.localview()

		job_name = addon_data.jobs[self.index].job_name
		self.report({'INFO'}, "Nifty FBX Exporter: Exported '{}' → {}".format(job_name, out_path))
		return {'FINISHED'}



# ─── Job management helpers ───────────────────────────────────────────────────

def _active_job(addon_data):
	"""Return the currently active NIFTYFBX_ExportJob, or raise if the index is invalid."""
	idx = addon_data.active_job_idx
	if idx < 0 or idx >= len(addon_data.jobs):
		raise RuntimeError("No valid job selected.")
	return addon_data.jobs[idx]


def _pick_collections(context):
	"""Identify the user's intended collection(s) using three escalating strategies:
	1) collections that own the selected viewport objects,
	2) collections directly highlighted in the Outliner (selected_ids),
	3) the active collection shown in the header drop-down.
	Returns a de-duplicated list; the Scene Collection is always excluded."""
	gathered = []
	seen = set()

	for obj in context.selected_objects:
		for col in obj.users_collection:
			if col is not context.scene.collection and col not in seen:
				gathered.append(col)
				seen.add(col)

	if not gathered and hasattr(context, 'selected_ids'):
		for block in context.selected_ids:
			if isinstance(block, bpy.types.Collection) and block is not context.scene.collection and block not in seen:
				gathered.append(block)
				seen.add(block)

	if not gathered:
		active = context.collection
		if active is not None and active is not context.scene.collection:
			gathered.append(active)

	return gathered


def _make_job_copy(src, jobs_collection):
	"""Append a new entry to jobs_collection and deep-copy every field from src into it.
	Returns the newly created job so the caller can adjust remaining fields (e.g. name)."""
	from . import properties as _props
	dst = jobs_collection.add()
	_props.clone_job(dst, src)
	return dst


def _resolve_job_objects(job, context):
	"""Ensure all job sources are visible, then return the complete set of objects
	the job references across its collections and direct object slots.
	Returns an empty set when no sources are assigned."""
	ensure_job_visible(context, job)
	result = set()
	for col_slot in job.collections:
		if col_slot.ref is not None:
			result.update(col_slot.ref.all_objects)
	for obj_slot in job.objects:
		if obj_slot.ref is not None:
			result.add(obj_slot.ref)
	return result


# ─── Job list operators ────────────────────────────────────────────────────────

class NIFTYFBX_OT_job_add(bpy.types.Operator):
	"""Append a new export job to the list"""
	bl_idname = "nifty_fbx_exporter.job_add"
	bl_label = "Add New Job"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		entry = addon_data.jobs.add()
		entry.job_name = "Job " + str(len(addon_data.jobs))
		addon_data.active_job_idx = len(addon_data.jobs) - 1
		return {'FINISHED'}


class NIFTYFBX_OT_job_add_from_collection(bpy.types.Operator):
	"""Create a new export job from the selected Collection"""
	bl_idname = "nifty_fbx_exporter.job_add_from_collection"
	bl_label = "Add Job from selected Collection"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		collections = _pick_collections(context)
		if not collections:
			show_error_popup("No selected collection (Scene Collection cannot be used).")
			return {'CANCELLED'}

		addon_data = context.scene.nifty_fbx_exporter
		entry = addon_data.jobs.add()
		entry.job_name = collections[0].name if len(collections) == 1 else "Multiple Collections"
		for col in collections:
			entry.collections.add().ref = col
		addon_data.active_job_idx = len(addon_data.jobs) - 1

		self.report({'INFO'}, "Nifty FBX Exporter: Created '{}' with: {}".format(
			entry.job_name, ", ".join(c.name for c in collections)))
		return {'FINISHED'}


class NIFTYFBX_OT_job_remove(bpy.types.Operator):
	"""Remove the selected export job from the list"""
	bl_idname = "nifty_fbx_exporter.job_remove"
	bl_label = "Remove Selected Job"
	bl_options = {'REGISTER', 'UNDO'}

	@classmethod
	def poll(cls, context):
		return context.scene.nifty_fbx_exporter.jobs

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		rm_idx = addon_data.active_job_idx
		addon_data.jobs.remove(rm_idx)
		remaining = len(addon_data.jobs)
		addon_data.active_job_idx = min(rm_idx, remaining - 1) if remaining > 0 else 0
		return {'FINISHED'}


class NIFTYFBX_OT_job_duplicate(bpy.types.Operator):
	"""Create a copy of the selected export job"""
	bl_idname = "nifty_fbx_exporter.job_duplicate"
	bl_label = "Duplicate Selected Job"
	bl_options = {'REGISTER', 'UNDO'}

	@classmethod
	def poll(cls, context):
		return context.scene.nifty_fbx_exporter.jobs

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		src = _active_job(addon_data)
		copy = _make_job_copy(src, addon_data.jobs)
		copy.job_name = src.job_name + " Copy"
		addon_data.active_job_idx = len(addon_data.jobs) - 1
		return {'FINISHED'}


class NIFTYFBX_OT_job_move(bpy.types.Operator):
	"""Reorder an export job in the list"""
	bl_idname = "nifty_fbx_exporter.job_move"
	bl_label = "Move Job"
	bl_options = {'REGISTER', 'UNDO'}
	direction: bpy.props.EnumProperty(items=(('UP', 'Up', ""), ('DOWN', 'Down', ""),))

	@classmethod
	def poll(cls, context):
		return context.scene.nifty_fbx_exporter.jobs

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		cur_idx = addon_data.active_job_idx
		dest    = cur_idx - 1 if self.direction == 'UP' else cur_idx + 1

		if dest < 0 or dest >= len(addon_data.jobs):
			return {'FINISHED'}

		# Move the active job to its destination slot
		addon_data.jobs.move(cur_idx, dest)
		addon_data.active_job_idx = dest
		return {'FINISHED'}


# ─── Source content operators (collections & objects) ─────────────────────────

class NIFTYFBX_OT_source_collection_add(bpy.types.Operator):
	"""Add an empty collection slot to the job"""
	bl_idname = "nifty_fbx_exporter.source_collection_add"
	bl_label = "Add Empty Collection Slot"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		_active_job(context.scene.nifty_fbx_exporter).collections.add()
		return {'FINISHED'}


class NIFTYFBX_OT_source_collection_add_selected(bpy.types.Operator):
	"""Add Selected Collection"""
	bl_idname = "nifty_fbx_exporter.source_collection_add_selected"
	bl_label = "Add Selected Collection"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		candidates = _pick_collections(context)
		if not candidates:
			show_error_popup("No selected collection (Scene Collection cannot be added).")
			return {'CANCELLED'}

		picked = candidates[0]
		col_list = _active_job(context.scene.nifty_fbx_exporter).collections

		if any(slot.ref == picked for slot in col_list):
			self.report({'INFO'}, "Nifty FBX Exporter: '{}' already in list".format(picked.name))
			return {'FINISHED'}

		col_list.add().ref = picked
		self.report({'INFO'}, "Nifty FBX Exporter: Added '{}'".format(picked.name))
		return {'FINISHED'}


class NIFTYFBX_OT_source_collection_remove(bpy.types.Operator):
	"""Remove a collection from the job"""
	bl_idname = "nifty_fbx_exporter.source_collection_remove"
	bl_label = "Remove Collection"
	bl_options = {'REGISTER', 'UNDO'}

	index: bpy.props.IntProperty(name="Collection Index to Remove", default=0)

	@classmethod
	def poll(cls, context):
		return context.scene.nifty_fbx_exporter.jobs

	@safe_execute
	def execute(self, context):
		_active_job(context.scene.nifty_fbx_exporter).collections.remove(self.index)
		return {'FINISHED'}


class NIFTYFBX_OT_source_object_add(bpy.types.Operator):
	"""Add an empty object slot to the job"""
	bl_idname = "nifty_fbx_exporter.source_object_add"
	bl_label = "Add Empty Object Slot"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		_active_job(context.scene.nifty_fbx_exporter).objects.add()
		return {'FINISHED'}


class NIFTYFBX_OT_source_object_add_selected(bpy.types.Operator):
	"""Add Selected Objects"""
	bl_idname = "nifty_fbx_exporter.source_object_add_selected"
	bl_label = "Add Selected Objects"
	bl_options = {'REGISTER', 'UNDO'}

	@safe_execute
	def execute(self, context):
		if not context.selected_objects:
			show_error_popup("No objects selected in the viewport.")
			return {'CANCELLED'}

		obj_list = _active_job(context.scene.nifty_fbx_exporter).objects
		existing = {slot.ref for slot in obj_list}
		new_objects = [obj for obj in context.selected_objects if obj not in existing]
		for obj in new_objects:
			obj_list.add().ref = obj

		self.report({'INFO'}, "Nifty FBX Exporter: Added {} object(s)".format(len(new_objects)))
		return {'FINISHED'}


class NIFTYFBX_OT_source_object_remove(bpy.types.Operator):
	"""Remove an object from the job"""
	bl_idname = "nifty_fbx_exporter.source_object_remove"
	bl_label = "Remove Object"
	bl_options = {'REGISTER', 'UNDO'}

	index: bpy.props.IntProperty(name="Object Index to Remove", default=0)

	@classmethod
	def poll(cls, context):
		return context.scene.nifty_fbx_exporter.jobs

	@safe_execute
	def execute(self, context):
		_active_job(context.scene.nifty_fbx_exporter).objects.remove(self.index)
		return {'FINISHED'}


# ─── Viewport selection / isolation operators ─────────────────────────────────

def _enter_object_mode(context):
	"""Switch to Object mode if the viewport is currently in another mode."""
	if context.view_layer.objects.active and context.view_layer.objects.active.mode != 'OBJECT':
		bpy.ops.object.mode_set(mode='OBJECT', toggle=False)


def _resolve_job_idx(operator_index, addon_data):
	"""Return the effective job index: use operator_index when explicitly set (≥ 0),
	otherwise fall back to the panel's active selection."""
	idx = operator_index if operator_index >= 0 else addon_data.active_job_idx
	if idx < 0 or idx >= len(addon_data.jobs):
		raise RuntimeError("No valid job selected.")
	return idx


class NIFTYFBX_OT_job_select(bpy.types.Operator):
	"""Select all objects and collection objects in the active job"""
	bl_idname = "nifty_fbx_exporter.job_select"
	bl_label = "Select Job Objects"
	bl_options = {'REGISTER', 'UNDO'}

	index: bpy.props.IntProperty(name="Index", default=-1)

	@classmethod
	def poll(cls, context):
		return len(context.scene.nifty_fbx_exporter.jobs) > 0

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		job = addon_data.jobs[_resolve_job_idx(self.index, addon_data)]
		objects = _resolve_job_objects(job, context)

		if not objects:
			show_error_popup("Job '{}' has no valid objects.".format(job.job_name))
			return {'CANCELLED'}

		if context.space_data and context.space_data.local_view:
			bpy.ops.view3d.localview()

		_enter_object_mode(context)
		bpy.ops.object.select_all(action='DESELECT')
		for obj in objects:
			obj.select_set(True)
		if context.selected_objects:
			context.view_layer.objects.active = context.selected_objects[0]

		self.report({'INFO'}, "Nifty FBX Exporter: Selected {} object(s)".format(len(objects)))
		return {'FINISHED'}


class NIFTYFBX_OT_job_isolate(bpy.types.Operator):
	"""Select and isolate job objects in local view"""
	bl_idname = "nifty_fbx_exporter.job_isolate"
	bl_label = "Isolate Job Objects"
	bl_options = {'REGISTER', 'UNDO'}

	index: bpy.props.IntProperty(name="Index", default=-1)

	@classmethod
	def poll(cls, context):
		return len(context.scene.nifty_fbx_exporter.jobs) > 0

	@safe_execute
	def execute(self, context):
		addon_data = context.scene.nifty_fbx_exporter
		job = addon_data.jobs[_resolve_job_idx(self.index, addon_data)]
		objects = _resolve_job_objects(job, context)

		if not objects:
			show_error_popup("Job '{}' has no valid objects.".format(job.job_name))
			return {'CANCELLED'}

		# Reset to full scene view before isolating, so local view contains exactly
		# the job objects and not a stale subset from a previous isolation.
		if context.space_data and context.space_data.local_view:
			bpy.ops.view3d.localview()

		_enter_object_mode(context)
		bpy.ops.object.select_all(action='DESELECT')
		for obj in objects:
			obj.select_set(True)
		if context.selected_objects:
			context.view_layer.objects.active = context.selected_objects[0]

		bpy.ops.view3d.localview()
		bpy.ops.object.select_all(action='DESELECT')

		self.report({'INFO'}, "Nifty FBX Exporter: Isolated {} object(s)".format(len(objects)))
		return {'FINISHED'}




# ─── Registration ─────────────────────────────────────────────────────────────

class NIFTYFBX_OT_open_github(bpy.types.Operator):
	bl_idname  = "nifty_fbx_exporter.open_github"
	bl_label   = "Open GitHub Repository"
	bl_description = "Open the Nifty FBX Exporter GitHub repository in your browser"

	def execute(self, context):
		bpy.ops.wm.url_open(url="https://github.com/AlexStock3D/Blender-Addon-Nifty-FBX-Exporter")
		return {'FINISHED'}


def register():
	bpy.utils.register_class(NIFTYFBX_OT_open_github)
	bpy.utils.register_class(NIFTYFBX_OT_apply_fbx_preset)
	bpy.utils.register_class(NIFTYFBX_OT_save_fbx_preset)
	bpy.utils.register_class(NIFTYFBX_OT_remove_fbx_preset)
	bpy.utils.register_class(NIFTYFBX_OT_export_single)
	bpy.utils.register_class(NIFTYFBX_OT_export_all)

	bpy.utils.register_class(NIFTYFBX_OT_job_add)
	bpy.utils.register_class(NIFTYFBX_OT_job_add_from_collection)
	bpy.utils.register_class(NIFTYFBX_OT_job_remove)
	bpy.utils.register_class(NIFTYFBX_OT_job_duplicate)
	bpy.utils.register_class(NIFTYFBX_OT_job_move)

	bpy.utils.register_class(NIFTYFBX_OT_source_collection_add)
	bpy.utils.register_class(NIFTYFBX_OT_source_collection_add_selected)
	bpy.utils.register_class(NIFTYFBX_OT_source_collection_remove)

	bpy.utils.register_class(NIFTYFBX_OT_source_object_add)
	bpy.utils.register_class(NIFTYFBX_OT_source_object_add_selected)
	bpy.utils.register_class(NIFTYFBX_OT_source_object_remove)

	bpy.utils.register_class(NIFTYFBX_OT_job_select)
	bpy.utils.register_class(NIFTYFBX_OT_job_isolate)

def unregister():
	bpy.utils.unregister_class(NIFTYFBX_OT_open_github)
	bpy.utils.unregister_class(NIFTYFBX_OT_apply_fbx_preset)
	bpy.utils.unregister_class(NIFTYFBX_OT_save_fbx_preset)
	bpy.utils.unregister_class(NIFTYFBX_OT_remove_fbx_preset)
	bpy.utils.unregister_class(NIFTYFBX_OT_export_single)
	bpy.utils.unregister_class(NIFTYFBX_OT_export_all)

	bpy.utils.unregister_class(NIFTYFBX_OT_job_add)
	bpy.utils.unregister_class(NIFTYFBX_OT_job_add_from_collection)
	bpy.utils.unregister_class(NIFTYFBX_OT_job_remove)
	bpy.utils.unregister_class(NIFTYFBX_OT_job_duplicate)
	bpy.utils.unregister_class(NIFTYFBX_OT_job_move)

	bpy.utils.unregister_class(NIFTYFBX_OT_source_collection_add)
	bpy.utils.unregister_class(NIFTYFBX_OT_source_collection_add_selected)
	bpy.utils.unregister_class(NIFTYFBX_OT_source_collection_remove)

	bpy.utils.unregister_class(NIFTYFBX_OT_source_object_add)
	bpy.utils.unregister_class(NIFTYFBX_OT_source_object_add_selected)
	bpy.utils.unregister_class(NIFTYFBX_OT_source_object_remove)

	bpy.utils.unregister_class(NIFTYFBX_OT_job_select)
	bpy.utils.unregister_class(NIFTYFBX_OT_job_isolate)
