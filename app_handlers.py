import bpy
import traceback
from bpy.app.handlers import persistent


# ─── Queue builders ───────────────────────────────────────────────────────────
# Each function accepts the addon state and returns the list of job indices
# that should run for that mode.  They are pure: no side effects, no I/O.

def _queue_all(addon_data):
	"""All jobs in the list, regardless of any flag."""
	return list(range(len(addon_data.jobs)))


def _queue_active(addon_data):
	"""Jobs whose 'Active' checkbox is enabled."""
	return [i for i, job in enumerate(addon_data.jobs) if job.is_active]


_QUEUE_BUILDERS = {
	"ALL":    _queue_all,
	"ACTIVE": _queue_active,
}


# ─── Scene state guards ───────────────────────────────────────────────────────

def _addon_state():
	"""Return the addon data from the active scene, or None if unavailable.
	Guards against the handler firing before the scene is ready."""
	try:
		return bpy.context.scene.nifty_fbx_exporter
	except Exception:
		return None


def _scene_is_exportable():
	"""Return True only when the .blend file has been saved to disk.
	Exporting from an unsaved file would produce paths like '/untitled.fbx'
	which is never what the user intends."""
	return bool(bpy.data.filepath)


# ─── Save handler ─────────────────────────────────────────────────────────────

@persistent
def _post_save_handler(scene):
	addon_data = _addon_state()
	if addon_data is None:
		return

	mode = addon_data.auto_export_mode
	if mode == "DISABLED":
		return

	print("\n[Nifty FBX Exporter] Save detected — auto-export mode: {}".format(mode))

	if not _scene_is_exportable():
		print("[Nifty FBX Exporter] Skipping — file has not been saved to disk yet")
		return

	if not addon_data.jobs:
		print("[Nifty FBX Exporter] Skipping — no jobs defined")
		return

	build_queue = _QUEUE_BUILDERS.get(mode)
	if build_queue is None:
		print("[Nifty FBX Exporter] Unknown auto-export mode '{}', skipping".format(mode))
		return

	queue = build_queue(addon_data)
	if not queue:
		print("[Nifty FBX Exporter] No jobs selected by mode '{}', skipping".format(mode))
		return

	print("[Nifty FBX Exporter] Running {} job(s)".format(len(queue)))

	# Import run_export_job lazily to avoid circular import at module load.
	from . import operators as _ops

	succeeded = []
	failed    = []

	for idx in queue:
		job_name = addon_data.jobs[idx].name
		try:
			_ops.run_export_job(bpy.context, addon_data, idx)
			succeeded.append(job_name)
			print("[Nifty FBX Exporter] OK  {}".format(job_name))
		except Exception as exc:
			traceback.print_exc()
			failed.append((job_name, str(exc)))
			print("[Nifty FBX Exporter] ERR {} — {}".format(job_name, exc))

	# Summary line
	if failed:
		names = ", ".join(n for n, _ in failed)
		print("[Nifty FBX Exporter] Finished {}/{} — failed: {}".format(
			len(succeeded), len(queue), names))
	else:
		print("[Nifty FBX Exporter] Finished {}/{} — all succeeded".format(
			len(succeeded), len(queue)))


# ─── Registration ─────────────────────────────────────────────────────────────

def register():
	handlers = bpy.app.handlers.save_post
	if _post_save_handler not in handlers:
		handlers.append(_post_save_handler)


def unregister():
	handlers = bpy.app.handlers.save_post
	if _post_save_handler in handlers:
		handlers.remove(_post_save_handler)
