import sys
import os
import json
import subprocess
import zipfile
import tempfile
import traceback
import re
import shutil
import urllib.request
import PyQt5.QtCore as QtCore
import PyQt5.QtWidgets as QtWidgets
import PIL.Image

VERSION = '0.1.0'
ENABLE_UPDATE_CHECKER = True

def get_executable_name(path):
  for f in os.listdir(path):
    if f.endswith('.exe') and f != 'notification_helper.exe':
      return f
  raise Exception('Cannot find executable')

def parse_package_json(path):
  with open(os.path.join(path, 'package.json')) as package_json_file:
    return json.load(package_json_file)

def run_command(args, check=True):
  completed = subprocess.run(
    args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    stdin=subprocess.PIPE,
    creationflags=subprocess.CREATE_NO_WINDOW,
    check=check
  )
  print(f'Command {completed.args} finished with code {completed.returncode}')
  return completed

def get_icon_as_ico(path: str) -> str:
  # Icon is normally stored as icon.png
  original_icon_name = 'icon.png'
  # ... Except in certain NW.js builds, where the path is in package.json
  package_json = parse_package_json(path)
  if 'window' in package_json:
    original_icon_name = package_json['window']['icon']
  original_icon_full_path = os.path.join(path, original_icon_name)
  image = PIL.Image.open(original_icon_full_path)
  ico_path = f'{original_icon_full_path}.ico'
  image.save(ico_path, format='ICO')
  return ico_path

def fix_icon(path: str):
  executable_file = os.path.join(path, get_executable_name(path))
  icon = get_icon_as_ico(path)
  run_command([
    os.path.join(os.path.dirname(__file__), 'third-party/rcedit/rcedit-x86.exe'),
    executable_file,
    '--set-icon',
    icon
  ])

def escape_html(string):
  return (
     string
      .replace('&', '&amp;')
      .replace('>', '&gt;')
      .replace('<', '&lt;')
      .replace('\'','&apos;')
      .replace('"','&quot;')
  )

def unescape_html(string):
  return (
    string
      .replace('&quot;', '"')
      .replace('&apos;', '\'')
      .replace('&lt;', '<')
      .replace('&gt;', '>')
      .replace('&amp;', '&')
  )

def get_project_title(path):
  with open(os.path.join(path, 'index.html')) as f:
    contents = f.read()
    title = re.search(r'<title>(.*)<\/title>', contents).group(1)
    return unescape_html(title)

def escape_inno_value(string):
  return (
    string
      .replace('{', '{{')
      .replace('"', '')
  )

def create_installer(path):
  executable_file = get_executable_name(path)
  package_json = parse_package_json(path)
  package_name = package_json['name']
  title = get_project_title(path)
  version = '1.0.0'
  output_directory = 'Generated Installer'
  output_name = f'{package_name} Setup'
  icon = get_icon_as_ico(path)
  inno_config = f"""; Automatically generated by TurboWarp. Avoid changing by hand.

#define TITLE "{escape_inno_value(title)}"
#define PACKAGE_NAME "{escape_inno_value(package_name)}"
#define EXECUTABLE "{escape_inno_value(executable_file)}"
#define VERSION "{escape_inno_value(version)}"

[Setup]
AppName={{#PACKAGE_NAME}}
AppVersion={{#VERSION}}
WizardStyle=classic
DefaultDirName={{autopf}}\{{#PACKAGE_NAME}}
UninstallDisplayIcon={{app}}\{{#EXECUTABLE}}
DefaultGroupName={{#TITLE}}
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
OutputDir={escape_inno_value(output_directory)}
OutputBaseFilename={escape_inno_value(output_name)}
SetupIconFile={escape_inno_value(icon)}

[Files]
Source: "*"; DestDir: "{{app}}"; Excludes: "*.iss"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{{group}}\{{#TITLE}}"; Filename: "{{app}}\{{#EXECUTABLE}}"

[Run]
Filename: "{{app}}\{{#EXECUTABLE}}"; Description: "Launch application"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{localappdata}}\{{#PACKAGE_NAME}}"
"""
  inno_config_path = os.path.join(path, 'config.iss')
  with open(inno_config_path, 'w') as f:
    f.write(inno_config)

  run_command([
    os.path.join(os.path.dirname(__file__), 'third-party/inno/iscc.exe'),
    inno_config_path
  ])

  expected_output_file = os.path.join(path, output_directory, f'{output_name}.exe')
  if not os.path.exists(expected_output_file):
    raise Exception('Did not output to expected spot')
  return expected_output_file

def get_zip_inner_folder_name(zip):
  info = zip.filelist[0]
  return info.filename.split('/')[0]

def display_success(message):
  print(message)
  msg = QtWidgets.QMessageBox()
  msg.setIcon(QtWidgets.QMessageBox.Information)
  msg.setWindowTitle('Success')
  msg.setText(message)
  msg.exec_()

def handle_error(error):
  traceback.print_exc()
  msg = QtWidgets.QMessageBox()
  msg.setIcon(QtWidgets.QMessageBox.Critical)
  msg.setWindowTitle('Error')
  msg.setText(str(error))
  msg.exec_()

def display_error(err):
  msg = QtWidgets.QMessageBox()
  msg.setIcon(QtWidgets.QMessageBox.Critical)
  msg.setWindowTitle('Error')
  msg.setText(err)
  msg.exec_()

def verify_folder(folder):
  # Make sure that a file used by both Electron and NW.js exists
  return os.path.exists(os.path.join(folder, 'resources.pak'))

def reveal_in_explorer(path):
  path = path.replace('/', '\\')
  print(f'Trying to reveal {path}')
  run_command([
    'explorer.exe',
    '/select,',
    path
  ], check=False)

class BaseThread(QtCore.QThread):
  error = QtCore.pyqtSignal(str)

  def run(self):
    try:
      self._run()
    except Exception as e:
      traceback.print_exc()
      self.error.emit(str(e))


class ExtractWorker(BaseThread):
  extracted = QtCore.pyqtSignal(str)

  def __init__(self, parent, filename, dest):
    super().__init__(parent)
    self.filename = filename
    self.dest = dest

  def _run(self):
    with zipfile.ZipFile(self.filename) as zip:
      zip.extractall(self.dest)
      extracted_contents = os.path.join(self.dest, get_zip_inner_folder_name(zip))
    print(f'Extracted to {extracted_contents}')
    if not verify_folder(extracted_contents):
      raise Exception('Invalid zip selected')
    self.extracted.emit(extracted_contents)


class OptionsWorker(BaseThread):
  progress_update = QtCore.pyqtSignal(str)
  success = QtCore.pyqtSignal()

  def __init__(self, parent):
    super().__init__(parent)
    self.temporary_directory = parent.temporary_directory.name
    self.extracted_contents = parent.extracted_contents
    self.filename = parent.filename
    self.should_fix_icon = parent.fix_icon_checkbox.isChecked()
    self.should_create_installer = parent.create_installer_checkbox.isChecked()
    if self.should_create_installer:
      self.installer_destination = parent.pick_installer_destination()

  def update_progress(self, text):
    print(text)
    self.progress_update.emit(text)

  def rezip(self):
    self.update_progress('Recompressing (slow!)')
    with tempfile.TemporaryFile() as temporary_archive:
      generated_archive_name = shutil.make_archive(temporary_archive.name, 'zip', self.temporary_directory)
      os.replace(generated_archive_name, self.filename)

  def _run(self):
    if self.should_fix_icon:
      self.update_progress('Fixing icon')
      fix_icon(self.extracted_contents)
      self.rezip()
    if self.should_create_installer:
      self.update_progress('Creating installer (very slow!!)')
      generated_installer_path = create_installer(self.extracted_contents)
      os.replace(generated_installer_path, self.installer_destination)
    self.success.emit()

class UpdateCheckerWorker(BaseThread):
  update_available = QtCore.pyqtSignal()

  def _run(self):
    with urllib.request.urlopen('https://raw.githubusercontent.com/TurboWarp/packager-extras/master/version.json') as response:
      contents = response.read()
      parsed = json.loads(contents)
      if parsed['latest'] != VERSION:
        self.update_available.emit()


class ExtractingWidget(QtWidgets.QWidget):
  def __init__(self):
    super().__init__()

    layout = QtWidgets.QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    self.setLayout(layout)

    label = QtWidgets.QLabel('Extracting...')
    label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
    label.setAlignment(QtCore.Qt.AlignCenter)
    layout.addWidget(label)


class ProgressWidget(QtWidgets.QWidget):
  def __init__(self):
    super().__init__()

    layout = QtWidgets.QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    self.setLayout(layout)

    label = QtWidgets.QLabel('This may take a while. Please be patient. Avoid closing the application until this process finishes.')
    label.setWordWrap(True)
    layout.addWidget(label)

    self.text_edit = QtWidgets.QTextEdit()
    self.text_edit.setReadOnly(True)
    self.text_edit.setFixedHeight(80)
    layout.addWidget(self.text_edit)

  def handle_progress_update(self, text):
    self.text_edit.append(text)


class ProjectOptionsWidget(QtWidgets.QWidget):
  process_started = QtCore.pyqtSignal()
  process_ended = QtCore.pyqtSignal()
  remove_me = QtCore.pyqtSignal()

  def __init__(self, filename):
    super().__init__()

    self.filename = filename
    self.temporary_directory = tempfile.TemporaryDirectory()

    layout = QtWidgets.QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    self.setLayout(layout)

    self.extracting_widget = ExtractingWidget()
    layout.addWidget(self.extracting_widget)
    self.progress_widget = None

    self.file_to_reveal = None

    extract_worker = ExtractWorker(self, self.filename, self.temporary_directory.name)
    extract_worker.error.connect(self.extract_worker_error)
    extract_worker.extracted.connect(self.finished_extract)
    extract_worker.start()

  def finished_extract(self, extracted_contents):
    self.process_ended.emit()

    self.extracting_widget.setParent(None)
    layout = self.layout()
    self.extracted_contents = extracted_contents

    label = QtWidgets.QLabel()
    label.setText(f'Opened: <b>{escape_html(os.path.basename(self.filename))}</b>')
    label.setFixedHeight(label.sizeHint().height())
    layout.addWidget(label)

    self.fix_icon_checkbox = QtWidgets.QCheckBox('Fix icon of .exe')
    self.fix_icon_checkbox.setChecked(True)
    layout.addWidget(self.fix_icon_checkbox)

    self.create_installer_checkbox = QtWidgets.QCheckBox('Create installer')
    self.create_installer_checkbox.setChecked(True)
    layout.addWidget(self.create_installer_checkbox)

    self.ok_button = QtWidgets.QPushButton('Continue')
    self.ok_button.clicked.connect(self.click)
    self.ok_button.setFixedHeight(self.ok_button.sizeHint().height() * 2)
    layout.addWidget(self.ok_button)

    self.cancel_button = QtWidgets.QPushButton('Go Back')
    self.cancel_button.clicked.connect(self.click_cancel)
    self.cancel_button.setFixedHeight(self.cancel_button.sizeHint().height())
    layout.addWidget(self.cancel_button)

  def pick_installer_destination(self):
    suggested_path = os.path.join(os.path.dirname(self.filename), f'{os.path.splitext(os.path.basename(self.filename))[0]} Setup.exe')
    installer_destination = QtWidgets.QFileDialog.getSaveFileName(self, 'Select where to save the installer', suggested_path, 'Executable files (*.exe)')[0]
    if not installer_destination:
      raise Exception('No file selected')
    self.file_to_reveal = installer_destination
    return installer_destination

  def set_enable_controls(self, enabled):
    if hasattr(self, 'fix_icon_checkbox'): self.fix_icon_checkbox.setVisible(enabled)
    if hasattr(self, 'create_installer_checkbox'): self.create_installer_checkbox.setVisible(enabled)
    if hasattr(self, 'ok_button'): self.ok_button.setVisible(enabled)
    if hasattr(self, 'cancel_button'): self.cancel_button.setVisible(enabled)

  def click(self):
    try:
      self.process_started.emit()

      fix_icon_checkbox = self.fix_icon_checkbox.isChecked()
      create_installer_checkbox = self.create_installer_checkbox.isChecked()
      if not fix_icon_checkbox and not create_installer_checkbox:
        raise Exception('You have to check at least one of the boxes')

      self.set_enable_controls(False)

      self.file_to_reveal = None
      worker = OptionsWorker(self)
      worker.error.connect(self.worker_error)
      worker.success.connect(self.worker_finished)

      self.progress_widget = ProgressWidget()
      self.layout().addWidget(self.progress_widget)

      worker.progress_update.connect(self.progress_widget.handle_progress_update)
      worker.start()
    except Exception as e:
      self.cleanup()
      handle_error(e)

  def click_cancel(self):
    self.remove()

  def cleanup(self):
    self.process_ended.emit()
    if self.progress_widget:
      self.progress_widget.setParent(None)
      self.progress_widget = None
    self.set_enable_controls(True)

  def extract_worker_error(self, err):
    self.worker_error(err)
    self.remove()

  def worker_error(self, err):
    display_error(err)
    self.cleanup()

  def worker_finished(self):
    display_success('Success')
    if self.file_to_reveal:
      reveal_in_explorer(self.file_to_reveal)
    self.cleanup()
    self.remove()

  def remove(self):
    self.temporary_directory.cleanup()
    self.remove_me.emit()


class SelectWidget(QtWidgets.QWidget):
  file_selected = QtCore.pyqtSignal(str)

  def __init__(self):
    super().__init__()

    layout = QtWidgets.QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    self.setLayout(layout)

    button = QtWidgets.QPushButton('Select or drop .zip file generated by packager')
    layout.addWidget(button)
    button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
    button.clicked.connect(self.click)

  def click(self):
    downloads_folder = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.DownloadLocation)
    file_result = QtWidgets.QFileDialog.getOpenFileName(self, 'Select packager output', downloads_folder, 'Zip files (*.zip)')
    file = file_result[0]
    if file:
      self.file_selected.emit(file)


class MainWindow(QtWidgets.QWidget):
  def __init__(self):
    super().__init__()

    self.resize(300, 200)
    self.setWindowTitle('Packager Extras')
    self.setWindowFlags(QtCore.Qt.WindowCloseButtonHint | QtCore.Qt.WindowMinimizeButtonHint)
    self.setAcceptDrops(True)

    layout = QtWidgets.QVBoxLayout()
    self.setLayout(layout)

    label = QtWidgets.QLabel('This is <b>beta software</b>. Make sure to <a href="https://github.com/TurboWarp/packager-extras/issues">report bugs</a>. Only run on files you trust.')
    label.setWordWrap(True)
    label.setFixedHeight(label.sizeHint().height())
    label.setOpenExternalLinks(True)
    layout.addWidget(label)

    self.select_widget = SelectWidget()
    self.select_widget.file_selected.connect(self.on_file_selected)
    layout.addWidget(self.select_widget)

    if ENABLE_UPDATE_CHECKER:
      self.update_checker_worker = UpdateCheckerWorker()
      self.update_checker_worker.update_available.connect(self.update_available)
      self.update_checker_worker.start()

    self.configure_widget = None
    self.is_process_ongoing = False

  def dragEnterEvent(self, event):
    if event.mimeData().hasUrls():
      event.accept()
    else:
      event.ignore()

  def dropEvent(self, event):
    file = event.mimeData().urls()[0].toLocalFile()
    if not self.is_process_ongoing:
      self.on_file_selected(file)

  def closeEvent(self, event):
    if self.is_process_ongoing:
      reply = QtWidgets.QMessageBox.question(
        self,
        'Confirm',
        'Are you sure you want to leave? The app is still running. We can\'t guarantee it will clean up properly if you close it preemptively.',
        QtWidgets.QMessageBox.Yes,
        QtWidgets.QMessageBox.No
      )
      if reply == QtWidgets.QMessageBox.Yes:
        event.accept()
      else:
        event.ignore()

  def on_file_selected(self, file):
    print(f'Opening {file}')
    try:
      if self.configure_widget:
        raise Exception('Already have a file open')
      self.is_process_ongoing = True
      self.configure_widget = ProjectOptionsWidget(file)
      self.configure_widget.remove_me.connect(self.on_project_done)
      self.configure_widget.process_started.connect(self.on_process_started)
      self.configure_widget.process_ended.connect(self.on_process_ended)
      self.layout().addWidget(self.configure_widget)
    except Exception as e:
      handle_error(e)
    else:
      self.select_widget.setParent(None)

  def on_process_started(self):
    self.is_process_ongoing = True

  def on_process_ended(self):
    self.is_process_ongoing = False

  def on_project_done(self):
    self.configure_widget.setParent(None)
    self.configure_widget.deleteLater()
    self.configure_widget = None
    self.layout().addWidget(self.select_widget)

  def update_available(self):
    print('An update is available')
    msg = QtWidgets.QMessageBox()
    msg.setIcon(QtWidgets.QMessageBox.Information)
    msg.setWindowTitle('Update Available')
    msg.setText('An update is available. Visit <a href="https://github.com/TurboWarp/packager-extras/releases">https://github.com/TurboWarp/packager-extras/releases</a> to find out more.')
    msg.exec_()


def main():
  os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
  app = QtWidgets.QApplication(sys.argv)
  window = MainWindow()
  window.show()
  sys.exit(app.exec_())


if __name__ == '__main__':
  main()
