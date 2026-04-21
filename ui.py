import bpy
from . import bl_info
from . import _preview_collections as _pcoll


# ─── Helpers ──────────────────────────────────────────────────────────# Thin wrappers used across both the main panel and any sub-panels.
# Keeping draw logic in one place avoids duplicating layout patterns.
def _section_label(layout, text, icon='NONE'):
	"""Draw a left-aligned section title row."""
	row = layout.row()
	row.alignment = 'LEFT'
	row.label(text=text, icon=icon)


def _toggle_row(layout, pg, text, icon='NONE', prop_name="is_open"):
	"""Draw a chevron toggle + label. Returns True when the section is open."""
	row = layout.row(align=True)
	is_open = getattr(pg, prop_name)
	chevron = 'TRIA_DOWN' if is_open else 'TRIA_RIGHT'
	row.prop(pg, prop_name, icon=chevron, icon_only=True, emboss=False)
	row.label(text=text, icon=icon)
	return is_open


FSPLIT = 0.45

def _prop_block(layout, pg, prop_names):
	"""Draw each property in prop_names as a two-column grid row inside layout."""
	col = layout.column(align=False)
	for name in prop_names:
		label = pg.bl_rna.properties[name].name
		row = col.split(factor=FSPLIT, align=True)
		row.label(text="  " + label)
		row.prop(pg, name, text="")


def _settings_section(parent, pg, title, icon, prop_names):
	"""Collapsible box containing a flat list of properties.  Returns the box
	so callers can append extra widgets after the standard property rows."""
	box = parent.box()
	if _toggle_row(box, pg, title, icon):
		_prop_block(box, pg, prop_names)
	return box


DOCS_URL = "https://github.com/AlexStock3D/Blender-Addon-Nifty-FBX-Exporter"

def _draw_logo(layout):
	"""Draw a Nifty Tools documentation button with custom icon."""
	pcoll = _pcoll.get("nifty")
	row = layout.row()
	row.alignment = 'CENTER'
	if pcoll and "nifty_btn" in pcoll:
		op = row.operator("nifty_fbx_exporter.open_github", text="Nifty Tools", icon_value=pcoll["nifty_btn"].icon_id)
	else:
		op = row.operator("nifty_fbx_exporter.open_github", text="Nifty Tools", icon='HOME')


# ─── Export config panel ──────────────────────────────────────────────────────────────
# Shared by both the per-package settings block and the global config override.
# preset_enabled=False is used when drawing the global config from the panel
# header area, where the preset row would overlap awkwardly.

def draw_export_settings(layout, settings, preset_enabled=True):
	"""Draw the full Export Config panel with collapsible sub-sections."""
	outer = layout.box()

	# Top-level toggle row
	hdr = outer.row(align=True)
	hdr.prop(
		settings, "is_open",
		icon='TRIA_DOWN' if settings.is_open else 'TRIA_RIGHT',
		icon_only=True, emboss=False,
	)
	hdr.label(text="FBX Export Settings", icon='EXPORT')

	if not settings.is_open:
		return

	body = outer.column(align=False)

	if preset_enabled:
		# Preset row: load dropdown on the left, save/remove buttons on the right.
		# The button label shows the active preset name when one is loaded.
		prow = body.row(align=True)
		prow.label(text="", icon='PRESET')
		plabel = settings.active_preset if settings.active_preset else "Load Preset"
		prow.operator_menu_enum(
			"nifty_fbx_exporter.apply_fbx_preset", "preset",
			text=plabel, icon='DOWNARROW_HLT',
		)
		prow.operator("nifty_fbx_exporter.save_fbx_preset",   text="", icon='ADD')
		prow.operator("nifty_fbx_exporter.remove_fbx_preset", text="", icon='REMOVE')
		body.separator(factor=0.4)

	_settings_section(body, settings.source_rules, "Misc", 'FILTER',
		["include_custom_props", "embed_textures"])

	# Object Filter — drawn expanded like the native FBX exporter
	obj_box = body.box()
	if _toggle_row(obj_box, settings.source_rules, "Include Object Types", 'OBJECT_DATA', 'filter_expanded'):
		obj_box.prop(settings.source_rules, "object_filter")

	_settings_section(body, settings.output_rules, "Transform", 'ORIENTATION_GIMBAL',
		["world_scale", "scale_method", "forward_axis", "world_up", "honor_scene_units", "bake_transforms"])

	_settings_section(body, settings.mesh_proc, "Geometry", 'MESH_DATA',
		["normals_mode", "preserve_subdivision", "apply_modifiers", "export_loose_edges", "tangent_space"])

	_settings_section(body, settings.rig_opts, "Armature", 'ARMATURE_DATA',
		["primary_axis", "secondary_axis", "root_node_type", "deform_only", "leaf_bones"])

	# Animation Bake — bake toggle gates the remaining props
	anim = settings.anim_bake
	anim_box = body.box()
	if _toggle_row(anim_box, anim, "Bake Animation", 'ACTION'):
		_prop_block(anim_box, anim, ["bake_enabled"])
		driven = anim_box.column(align=False)
		driven.enabled = anim.bake_enabled
		_prop_block(driven, anim, ["key_all_bones", "nla_strips", "all_actions",
			"force_frame_range", "frame_step", "simplify_factor"])


# ─── Main Panel ──────────────────────────────────────────────────────

class NIFTYFBX_PT_panel(bpy.types.Panel):
	bl_idname = 'NIFTYFBX_PT_panel'
	bl_label = 'Nifty FBX Exporter v' + '.'.join(str(v) for v in bl_info["version"])
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'Nifty FBX Exporter'

	def draw(self, context):
		layout = self.layout
		scene = context.scene
		data = scene.nifty_fbx_exporter
		jobs = data.jobs
		idx = data.active_job_idx
		has_selection = idx >= 0 and len(jobs) > idx

		# ── Export buttons ──────────────────────────────────────────
		action_box = layout.box()
		_section_label(action_box, "Export", 'EXPORT')

		row = action_box.row(align=True)
		row.scale_y = 1.4

		sub = row.row(align=True)
		sub.enabled = has_selection
		op = sub.operator(
			"nifty_fbx_exporter.export_single",
			text="Export Selected",
		)
		if has_selection:
			op.index = idx

		export_all_label = "Export All Actives" if data.show_active_checkboxes else "Export All"
		row.operator(
			"nifty_fbx_exporter.export_all",
			text=export_all_label,
		)

		# ── Job list + sidebar buttons ────────────────────────────────────
		action_box.separator(factor=0.4)
		_section_label(action_box, "Export Packages", 'PACKAGE')

		row = action_box.row()
		col_list = row.column()
		col_list.template_list(
			"NIFTYFBX_UL_job_list",
			"nifty_fbx_exporter_jobs",
			data, "jobs",
			data, "active_job_idx",
			rows=7,
		)

		col_btns = row.column(align=True)
		col_btns.operator("nifty_fbx_exporter.job_add",                 text="", icon="ADD")
		col_btns.operator("nifty_fbx_exporter.job_remove",              text="", icon="REMOVE")
		col_btns.separator()
		col_btns.operator("nifty_fbx_exporter.job_add_from_collection", text="", icon="COLLECTION_NEW")
		col_btns.operator("nifty_fbx_exporter.job_duplicate",           text="", icon="DUPLICATE")
		col_btns.separator()
		col_btns.operator("nifty_fbx_exporter.job_move", text="", icon="TRIA_UP").direction   = 'UP'
		col_btns.operator("nifty_fbx_exporter.job_move", text="", icon="TRIA_DOWN").direction = 'DOWN'
		col_btns.separator()
		checkbox_icon = 'CHECKBOX_HLT' if data.show_active_checkboxes else 'CHECKBOX_DEHLT'
		col_btns.prop(data, "show_active_checkboxes", text="", icon=checkbox_icon)

		# ── Global Export Settings (collapsible) ───────────────────────
		action_box.separator(factor=0.4)
		if _toggle_row(action_box, data, "Master Export Settings", 'TOOL_SETTINGS', "global_opts_visible"):
			gcol = action_box.column(align=True)
			GSPLIT = 0.35
			gr1 = gcol.split(factor=GSPLIT, align=True)
			gr1.label(text="  Auto-Export")
			gr1.prop(data, "auto_export_mode", text="")
			gr2 = gcol.split(factor=GSPLIT, align=True)
			gr2.label(text="  Prefix")
			gr2.prop(data, "output_prefix", text="")
			gr3 = gcol.split(factor=GSPLIT, align=True)
			gr3.label(text="  Suffix")
			gr3.prop(data, "output_suffix", text="")

			# Global output path toggle
			path_box = gcol.box()
			ph = path_box.row(align=False)
			path_icon = 'CHECKBOX_HLT' if data.use_global_output_dir else 'CHECKBOX_DEHLT'
			ph.prop(data, "use_global_output_dir", text="", icon=path_icon)
			ph.label(text="Use Master Export Path")
			if data.use_global_output_dir:
				path_box.prop(data, "global_output_dir", text="", icon='FILE_FOLDER')

			# Global export config toggle — when on, per-job settings are
			# greyed out and the config drawn here applies to every job.
			cfg_box = gcol.box()
			ch = cfg_box.row(align=False)
			cfg_icon = 'CHECKBOX_HLT' if data.use_global_config else 'CHECKBOX_DEHLT'
			ch.prop(data, "use_global_config", text="", icon=cfg_icon)
			ch.label(text="Use Master FBX Export Settings")
			if data.use_global_config:
						draw_export_settings(cfg_box, data.global_export_config)
		if not has_selection:
			_draw_logo(layout)
			return

		job = jobs[idx]

		layout.separator(factor=0.5)

		# ── Per-job settings ────────────────────────────────────────
		job_section = layout.box()
		if not _toggle_row(job_section, job, "'" + job.job_name + "' Export Settings", 'PREFERENCES'):
			_draw_logo(layout)
			return

		job_box = job_section

		# General
		_section_label(job_box, "General", 'PROPERTIES')
		col = job_box.column(align=True)
		SPLIT = 0.35

		r1 = col.split(factor=SPLIT, align=True)
		r1.label(text="  Name")
		r1.prop(job, "job_name", text="")

		r2 = col.split(factor=SPLIT, align=True)
		r2.label(text="  Output")
		r2.prop(job, "output_dir", text="")

		if data.use_global_output_dir:
			info = col.split(factor=SPLIT, align=True)
			info.enabled = False
			info.label(text="")
			info.label(text="Using master export path")

		# Filename preview
		preview_name = data.output_prefix + job.job_name + data.output_suffix + ".fbx"
		r3 = col.split(factor=SPLIT, align=True)
		r3.enabled = False
		r3.label(text="  Preview")
		preview_right = r3.row()
		preview_right.alignment = 'LEFT'
		preview_right.label(text=preview_name, icon='FILE')

		job_box.separator(factor=0.4)

		# Export Content (collapsible)
		sources_box = job_box.box()
		if _toggle_row(sources_box, job, "Export Content", 'SCENE_DATA', "sources_expanded"):
			# Export Collections
			col_box = sources_box.box()
			col_items = job.collections
			col_header = col_box.row(align=True)
			col_header.label(text="Export Collections  (" + str(len(col_items)) + ")", icon='OUTLINER_COLLECTION')
			col_header.operator("nifty_fbx_exporter.source_collection_add_selected", text="", icon="COLLECTION_NEW")
			col_header.operator("nifty_fbx_exporter.source_collection_add",          text="", icon="ADD")
			if len(col_items) == 0:
				r = col_box.row()
				r.alignment = 'CENTER'
				r.enabled = False
				r.label(text="None")
			else:
				col_col = col_box.column(align=True)
				for ci in range(len(col_items)):
					r = col_col.row(align=True)
					r.prop(col_items[ci], "ref", text="")
					r.operator("nifty_fbx_exporter.source_collection_remove", text="", icon="X").index = ci

			# Export Objects
			obj_box = sources_box.box()
			obj_items = job.objects
			obj_header = obj_box.row(align=True)
			obj_header.label(text="Export Objects  (" + str(len(obj_items)) + ")", icon='OBJECT_DATA')
			obj_header.operator("nifty_fbx_exporter.source_object_add_selected", text="", icon="OBJECT_DATA")
			obj_header.operator("nifty_fbx_exporter.source_object_add",          text="", icon="ADD")
			if len(obj_items) == 0:
				r = obj_box.row()
				r.alignment = 'CENTER'
				r.enabled = False
				r.label(text="None")
			else:
				obj_col = obj_box.column(align=True)
				for oi in range(len(obj_items)):
					r = obj_col.row(align=True)
					r.prop(obj_items[oi], "ref", text="")
					r.operator("nifty_fbx_exporter.source_object_remove", text="", icon="X").index = oi

		# Relocation During Export (collapsible)
		transform_box = job_box.box()
		if _toggle_row(transform_box, job, "Relocation", 'EMPTY_AXIS', "transform_expanded"):
			desc_row = transform_box.row()
			desc_row.enabled = False
			desc_col = desc_row.column()
			desc_col.scale_y = 0.7
			desc_col.label(text="Moves the object to a chosen world position")
			desc_col.label(text="during export. Children follow along.")
			desc_col.label(text="Original position is restored afterwards.")

			transform_box.separator(factor=0.5)

			transform_col = transform_box.column(align=False)
			transform_col.prop(job, "transform_enabled", toggle=True,
				icon='CHECKBOX_HLT' if job.transform_enabled else 'CHECKBOX_DEHLT')
			transform_col.separator(factor=0.5)
			sub = transform_col.column(align=False)
			sub.enabled = job.transform_enabled
			sub.prop(job, "transform_object", text="Object")
			sub.separator(factor=0.3)
			sub.prop(job, "transform_origin", text="")

		# FBX Export Settings — shown greyed-out with an explanation label when
		# the global config override is active, so users know why it's disabled.
		if data.use_global_config:
			fbx_wrapper = job_box.box()
			fbx_wrapper.enabled = False
			fbx_row = fbx_wrapper.row(align=True)
			fbx_row.label(text="", icon='TRIA_RIGHT')
			fbx_row.label(text="FBX Export Settings", icon='EXPORT')
			info = fbx_wrapper.row()
			info.alignment = 'CENTER'
			info.label(text="Using global export settings")
		else:
			draw_export_settings(job_box, job.export_cfg)

		# Nifty logo — bottom
		_draw_logo(layout)


# ─── UIList ──────────────────────────────────────────────────────────────────────

class NIFTYFBX_UL_job_list(bpy.types.UIList):
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
		row = layout.row(align=True)
		if data.show_active_checkboxes:
			row.prop(item, "is_active", text="")
		row.prop(item, "job_name", text="", icon='PACKAGE', emboss=False)
		# Inline select/isolate buttons let the user preview job contents
		# without leaving the panel.
		op_select = row.operator("nifty_fbx_exporter.job_select",  text="", icon='RESTRICT_SELECT_OFF')
		op_select.index = index
		op_isolate = row.operator("nifty_fbx_exporter.job_isolate", text="", icon='ZOOM_SELECTED')
		op_isolate.index = index


# ─── Addon Preferences ───────────────────────────────────────────────

class NIFTYFBX_AddonPreferences(bpy.types.AddonPreferences):
	bl_idname = __package__

	def draw(self, context):
		layout = self.layout
		layout.operator("wm.url_open", text="Documentation", icon='URL').url = "https://github.com/AlexStock3D/Blender_nifty_fbx_exporter"


def register():
	bpy.utils.register_class(NIFTYFBX_AddonPreferences)
	bpy.utils.register_class(NIFTYFBX_PT_panel)
	bpy.utils.register_class(NIFTYFBX_UL_job_list)

def unregister():
	bpy.utils.unregister_class(NIFTYFBX_AddonPreferences)
	bpy.utils.unregister_class(NIFTYFBX_PT_panel)
	bpy.utils.unregister_class(NIFTYFBX_UL_job_list)