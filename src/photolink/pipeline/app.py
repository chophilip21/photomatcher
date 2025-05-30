"""Divide functional and UI related logic."""

import json
import sys
import time
from pathlib import Path

from loguru import logger
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QLabel, QMessageBox

import photolink.pipeline.settings as settings
import photolink.utils.enums as enums
from photolink import get_application_path, get_config
from photolink.pipeline.front import MainWindowFront, ProcessWidget
from photolink.pipeline.qss import *
from photolink.utils.function import search_all_images
from photolink.workers.worker import Worker


class MainWindow(MainWindowFront):
    """All functional codes related to Pyside go here."""

    def __init__(self):

        super().__init__()
        self.application_path = get_application_path()
        self.pipeline_path = self.application_path / "src" / "photolink" / "pipeline"
        self.config = get_config()
        self.venv_path = self.application_path / Path(
            self.config["WINDOWS"]["VIRTUAL_ENV"]
        )
        self.job = {}
        self.operating_system = sys.platform
        self.drawUI()
        self.current_progress = 0
        self.threads = []

        # setup log path.
        logger.info(f"Application path: {self.application_path}")
        logger.info(f"Cache dir: {self.cache_dir}")
        logger.info(f"Operating system: {self.operating_system}")

        # settings related signals.
        self.settings = settings.signals_object
        self.settings.saved.connect(self.notify_settings_saved)
        self.settings.cache_deleted.connect(self.notify_cache_deleted)

    @Slot()
    def handle_box_click(self):
        clicked_button = self.sender()
        task = clicked_button.findChild(QLabel).text()
        self.select_task(task)

    def select_task(self, task):
        if task == "Face Search":
            self.instruction_label.setText(enums.Task.FACE_SEARCH.value)
            self.reference_path_selector.line_edit.setPlaceholderText("")
            self.reference_path_selector.button.setEnabled(True)
            self.current_task = enums.Task.FACE_SEARCH.name

        elif task == "Cluster":
            self.instruction_label.setText(enums.Task.CLUSTERING.value)
            self.reference_path_selector.line_edit.setPlaceholderText(
                "Not required for clustering"
            )
            # clean up reference path text
            self.reference_path_selector.line_edit.setText("")
            self.reference_path_selector.button.setEnabled(False)
            self.current_task = enums.Task.CLUSTERING.name

        elif task == "DP2 Match":
            self.instruction_label.setText(enums.Task.DP2_MATCH.value)
            self.reference_path_selector.line_edit.setPlaceholderText("")
            self.reference_path_selector.button.setEnabled(True)
            self.current_task = enums.Task.DP2_MATCH.name

        # Reset border colors for both boxes
        self.sample_match_box.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {self.matching_color[0]}, stop:1 {self.matching_color[1]}); border: 2px solid black;"
        )
        self.cluster_box.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {self.clustering_color[0]}, stop:1 {self.clustering_color[1]}); border: 2px solid black;"
        )

        self.dp2_box.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {self.dp2_color[0]}, stop:1 {self.dp2_color[1]}); border: 2px solid black;"
        )

        # Highlight the selected box
        if task == "Face Search":
            self.sample_match_box.setStyleSheet(
                self.sample_match_box.styleSheet() + " border: 2px solid white;"
            )
        elif task == "Cluster":
            self.cluster_box.setStyleSheet(
                self.cluster_box.styleSheet() + " border: 2px solid white;"
            )
        elif task == "DP2 Match":
            self.dp2_box.setStyleSheet(
                self.dp2_box.styleSheet() + " border: 2px solid white;"
            )

    def process_jobs(self):
        """Call the multiprocessing method when the start button is clicked."""
        self.change_button_status(False)
        self.init_time = time.time()
        self.log_message("Processing started.")

        # Check output path. Universal check for all tasks.
        if not self.output_path_selector.line_edit.text():
            self.display_notification(
                enums.ErrorMessage.PATH_NOT_SELECTED.name,
                enums.ErrorMessage.PATH_NOT_SELECTED.value,
            )

            self.change_button_status(True)
            return

        self.job["output"] = self.output_path_selector.line_edit.text()

        # start by generating jobs based on the selected task
        if (
            self.current_task == enums.Task.FACE_SEARCH.name
            or self.current_task == enums.Task.DP2_MATCH.name
        ):

            if (
                not self.source_path_selector.line_edit.text()
                or not self.reference_path_selector.line_edit.text()
            ):
                self.display_notification(
                    enums.ErrorMessage.PATH_NOT_SELECTED.name,
                    enums.ErrorMessage.PATH_NOT_SELECTED.value,
                )
                self.change_button_status(True)
                return

            self.job["task"] = self.current_task
            self.job["source"] = search_all_images(
                self.source_path_selector.line_edit.text()
            )
            self.job["reference"] = search_all_images(
                self.reference_path_selector.line_edit.text()
            )

        elif self.current_task == enums.Task.CLUSTERING.name:

            # only source path is required for clustering.
            if not self.source_path_selector.line_edit.text():
                self.display_notification(
                    enums.ErrorMessage.PATH_NOT_SELECTED.name,
                    enums.ErrorMessage.PATH_NOT_SELECTED.value,
                )
                self.change_button_status(True)
                return

            self.job["task"] = self.current_task
            self.job["source"] = search_all_images(
                self.source_path_selector.line_edit.text()
            )
        else:
            self.change_button_status(True)
            raise ValueError("Invalid task selected")

        # Now passed all validation, so display the progress bar.
        self.progress_widget = ProcessWidget(self.stop_processing)
        self.progress_message_box = QMessageBox(self)
        self.progress_message_box.setWindowTitle("Processing has started. Please wait.")
        self.progress_message_box.setStandardButtons(QMessageBox.NoButton)
        self.progress_message_box.layout().addWidget(self.progress_widget)
        self.progress_message_box.setGeometry(500, 300, 400, 400)
        self.progress_message_box.show()

        # proceed to dump the job to a json file for worker nodes.
        job_json = self.cache_dir / "job.json"
        with open(job_json, "w") as f:
            json.dump(self.job, f)

        # start the worker on a thread. This will prevent the GUI from freezing.
        worker = Worker(self.job["task"])
        worker.signals.stopped.connect(self.task_interrupted)
        worker.signals.result.connect(self.task_result)
        worker.signals.finished.connect(self.task_finished)
        worker.signals.error.connect(self.task_error)
        worker.start()
        self.threads.append(worker)

    def task_interrupted(self):
        """Called when the process is stopped by user"""
        self.stop_processing()
        self.display_notification("Stopped", "All operations stopped.")
        self.log_message("Processing stopped.")
        self.change_button_status(True)

    def task_finished(self):
        """Called when the Processing is finished."""
        self.stop_processing()
        self.change_button_status(True)
        self.display_notification("Complete", "All operations completed successfully.")
        self.log_message("Processing finished.")

    def stop_processing(self):
        """Universal stop mechanism for the processing."""
        self.progress_widget.movie.stop()
        self.progress_widget.timer.stop()
        for thread in self.threads:
            thread.stop()
        self.progress_message_box.accept()
        self.num_preprocessed = 0
        self.num_postprocessed = 0
        self.current_progress = 0

    def task_error(self, error):
        """Called when an error has occured during processing."""
        logger.error(f"Error has occured on the thread: {error}")
        self.display_notification(
            "Error", f"An error has occured during processing: {error}"
        )
        self.stop_processing()

    def task_result(self, result):
        """Called to log task results to console."""
        self.log_message(f"\n SYSTEM MESSAGE:  {result}")

    def notify_settings_saved(self):
        """Called when the settings dialog is saved."""
        self.log_message("Settings saved successfully.")

    def notify_cache_deleted(self):
        """Called when the cache is deleted."""
        self.log_message("Cache deleted successfully.")
