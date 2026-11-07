"""Safe output helpers used by the image-processing operations."""
import os
import shutil
import tempfile


def atomic_write(destination, writer):
    """Write to a sibling temporary file, then atomically replace destination."""
    destination = os.path.abspath(destination)
    directory = os.path.dirname(destination)
    if not os.path.isdir(directory):
        raise FileNotFoundError("Output directory does not exist: " + directory)

    fd, temporary_path = tempfile.mkstemp(
        prefix=".radartool-write-", suffix=".tmp", dir=directory
    )
    os.close(fd)
    try:
        writer(temporary_path)
        if not os.path.isfile(temporary_path):
            raise RuntimeError("The output writer did not create its temporary file.")
        with open(temporary_path, "rb") as written:
            os.fsync(written.fileno())
        os.replace(temporary_path, destination)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        except OSError:
            pass


def commit_staged_files(staging_directory, output_directory, filenames):
    """Commit a batch of files, restoring replaced files if a commit fails.

    Both directories must be on the same filesystem. The staging directory is
    expected to be created inside output_directory.
    """
    staging_directory = os.path.abspath(staging_directory)
    output_directory = os.path.abspath(output_directory)
    if not os.path.isdir(staging_directory) or not os.path.isdir(output_directory):
        raise FileNotFoundError("The staging or output directory is missing.")

    names = []
    for filename in filenames:
        filename = str(filename)
        if not filename or filename in (".", "..") or os.path.basename(filename) != filename:
            raise ValueError("Unsafe output filename: " + repr(filename))
        source = os.path.join(staging_directory, filename)
        if not os.path.isfile(source):
            raise FileNotFoundError("Staged output is missing: " + filename)
        names.append(filename)

    if not names:
        return 0

    backup_directory = tempfile.mkdtemp(
        prefix=".radartool-backup-", dir=output_directory
    )
    backed_up = []
    installed = []
    try:
        for filename in names:
            destination = os.path.join(output_directory, filename)
            if os.path.exists(destination):
                backup_path = os.path.join(backup_directory, filename)
                os.replace(destination, backup_path)
                backed_up.append(filename)

        for filename in names:
            os.replace(
                os.path.join(staging_directory, filename),
                os.path.join(output_directory, filename),
            )
            installed.append(filename)

    except Exception as commit_error:
        rollback_errors = []
        for filename in reversed(installed):
            try:
                os.unlink(os.path.join(output_directory, filename))
            except FileNotFoundError:
                pass
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))

        for filename in reversed(backed_up):
            try:
                os.replace(
                    os.path.join(backup_directory, filename),
                    os.path.join(output_directory, filename),
                )
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))

        if rollback_errors:
            # Never delete the backup directory if restoration was incomplete.
            raise RuntimeError(
                "Output commit failed and rollback was incomplete. "
                "Original files are preserved where possible in: "
                + backup_directory + ". Rollback errors: "
                + "; ".join(rollback_errors)
            ) from commit_error
        shutil.rmtree(backup_directory, ignore_errors=True)
        raise

    else:
        shutil.rmtree(backup_directory, ignore_errors=True)
        return len(installed)
