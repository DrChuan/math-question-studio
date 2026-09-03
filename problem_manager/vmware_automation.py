from __future__ import annotations

import os
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import zipfile


VMRUN_PATH = "/Applications/VMware Fusion.app/Contents/Public/vmrun"
DEFAULT_VMX_PATH = os.environ.get("PROBLEM_MANAGER_VMX_PATH", "")
DEFAULT_GUEST_WORKDIR = r"C:\Users\Public\Documents\ProblemManager"
DEFAULT_CONVERT_EQNS_CANDIDATES = [
    "MTCommand_ConvertEqns",
    "MathType Commands 2016.dotm!MTCommand_ConvertEqns",
    "MathType Commands 2013.dotm!MTCommand_ConvertEqns",
    "MathType Commands 2010.dotm!MTCommand_ConvertEqns",
    "MathType Commands.dotm!MTCommand_ConvertEqns",
    "MathType Commands.dot!MTCommand_ConvertEqns",
    "WordCmds.dot!MTCommand_ConvertEqns",
    "MTCommand_OnConvertEquations",
    "MathType Commands 2016.dotm!MTCommand_OnConvertEquations",
    "MathType Commands 2013.dotm!MTCommand_OnConvertEquations",
    "MathType Commands 2010.dotm!MTCommand_OnConvertEquations",
    "MathType Commands.dotm!MTCommand_OnConvertEquations",
    "MathType Commands.dot!MTCommand_OnConvertEquations",
    "WordCmds.dot!MTCommand_OnConvertEquations",
]
DEFAULT_TEX_TOGGLE_CANDIDATES = [
    "MTCommand_TeXToggle",
    "MathType Commands 2016.dotm!MTCommand_TeXToggle",
    "MathType Commands 2013.dotm!MTCommand_TeXToggle",
    "MathType Commands 2010.dotm!MTCommand_TeXToggle",
    "MathType Commands.dotm!MTCommand_TeXToggle",
    "MathType Commands.dot!MTCommand_TeXToggle",
    "WordCmds.dot!MTCommand_TeXToggle",
    "MTCommand_OnTeXToggle",
    "MathType Commands 2016.dotm!MTCommand_OnTeXToggle",
    "MathType Commands 2013.dotm!MTCommand_OnTeXToggle",
    "MathType Commands 2010.dotm!MTCommand_OnTeXToggle",
    "MathType Commands.dotm!MTCommand_OnTeXToggle",
    "MathType Commands.dot!MTCommand_OnTeXToggle",
    "WordCmds.dot!MTCommand_OnTeXToggle",
]


class VmAutomationError(RuntimeError):
    pass


@dataclass
class VmAutomationConfig:
    vmx_path: str
    vm_password: str
    guest_username: str
    guest_password: str
    guest_workdir: str
    word_macro_name: str = ""

    def to_profile(self) -> dict[str, str]:
        return {
            "vmx_path": self.vmx_path,
            "guest_username": self.guest_username,
            "guest_workdir": self.guest_workdir,
            "word_macro_name": self.word_macro_name,
        }


@dataclass
class VmAutomationResult:
    host_output_path: Path
    guest_output_path: str


def export_via_vmware_mathtype(
    host_input_path: Path,
    host_output_path: Path,
    config: VmAutomationConfig,
    use_tex_toggle: bool = False,
) -> VmAutomationResult:
    host_input_path = Path(host_input_path)
    host_output_path = Path(host_output_path)
    _validate_config(config)
    if not use_tex_toggle:
        return _run_single_vmware_export(host_input_path, host_output_path, config, use_tex_toggle=False)

    remaining_tex_count = _count_tex_formulas_in_docx(host_input_path)
    max_passes = max(1, min(remaining_tex_count + 1, 80))
    temp_dir = Path(tempfile.mkdtemp(prefix="problem_manager_vmware_passes_"))
    current_input_path = host_input_path
    final_result: VmAutomationResult | None = None
    try:
        for pass_index in range(max_passes):
            pass_output_path = temp_dir / f"pass_{pass_index + 1}.docx"
            final_result = _run_single_vmware_export(
                current_input_path,
                pass_output_path,
                config,
                use_tex_toggle=True,
            )
            current_input_path = final_result.host_output_path
            if _count_tex_formulas_in_docx(current_input_path) == 0:
                break

        host_output_path.parent.mkdir(parents=True, exist_ok=True)
        if current_input_path != host_output_path:
            shutil.copyfile(current_input_path, host_output_path)
        return VmAutomationResult(host_output_path=host_output_path, guest_output_path=final_result.guest_output_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_single_vmware_export(
    host_input_path: Path,
    host_output_path: Path,
    config: VmAutomationConfig,
    use_tex_toggle: bool,
) -> VmAutomationResult:
    vmx_path = config.vmx_path
    guest_input_path = _guest_join(config.guest_workdir, "problem_manager_input.docx")
    guest_output_path = _guest_join(config.guest_workdir, "problem_manager_output.docx")
    guest_script_path = _guest_join(config.guest_workdir, "problem_manager_convert.ps1")
    guest_log_path = _guest_join(config.guest_workdir, "problem_manager_log.txt")

    _run_vmrun(["start", vmx_path, "gui"], config, guest_auth=False, tolerated_errors=["already powered on"])
    _wait_for_tools(vmx_path, config)
    _run_vmrun(
        ["createDirectoryInGuest", vmx_path, config.guest_workdir],
        config,
        guest_auth=True,
        tolerated_errors=["file already exists", "cannot create a file when that file already exists"],
    )
    _run_vmrun(
        ["deleteFileInGuest", vmx_path, guest_output_path],
        config,
        guest_auth=True,
        tolerated_errors=["file not found", "cannot find the file", "a file was not found"],
    )
    _run_vmrun(
        ["deleteFileInGuest", vmx_path, guest_log_path],
        config,
        guest_auth=True,
        tolerated_errors=["file not found", "cannot find the file", "a file was not found"],
    )
    _run_vmrun(["CopyFileFromHostToGuest", vmx_path, str(host_input_path), guest_input_path], config, guest_auth=True)

    script_file = _build_guest_script(config.word_macro_name, use_tex_toggle)
    try:
        _run_vmrun(["CopyFileFromHostToGuest", vmx_path, str(script_file), guest_script_path], config, guest_auth=True)
        _run_vmrun(
            [
                "runProgramInGuest",
                vmx_path,
                "-interactive",
                "-noWait",
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                guest_script_path,
                guest_input_path,
                guest_output_path,
                guest_log_path,
            ],
            config,
            guest_auth=True,
        )
        _wait_for_guest_file(vmx_path, guest_output_path, config)
    finally:
        script_file.unlink(missing_ok=True)

    host_output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_vmrun(["CopyFileFromGuestToHost", vmx_path, guest_output_path, str(host_output_path)], config, guest_auth=True)
    return VmAutomationResult(host_output_path=host_output_path, guest_output_path=guest_output_path)


def _count_tex_formulas_in_docx(file_path: Path) -> int:
    try:
        with zipfile.ZipFile(file_path) as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except (FileNotFoundError, KeyError, zipfile.BadZipFile):
        return 0

    dollar_matches = re.findall(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", document_xml)
    paren_matches = re.findall(r"\\\(.+?\\\)", document_xml)
    return len(dollar_matches) + len(paren_matches)


def _validate_config(config: VmAutomationConfig) -> None:
    missing = []
    if not config.vmx_path.strip():
        missing.append("VMX 路径")
    if not config.vm_password.strip():
        missing.append("虚拟机加密密码")
    if not config.guest_username.strip():
        missing.append("Windows 用户名")
    if not config.guest_password.strip():
        missing.append("Windows 密码")
    if not config.guest_workdir.strip():
        missing.append("Windows 工作目录")
    if missing:
        raise VmAutomationError("以下信息不能为空：" + "、".join(missing))


def _run_vmrun(
    args: list[str],
    config: VmAutomationConfig,
    guest_auth: bool,
    tolerated_errors: list[str] | None = None,
) -> str:
    command = [VMRUN_PATH, "-T", "fusion"]
    if config.vm_password:
        command.extend(["-vp", config.vm_password])
    if guest_auth:
        command.extend(["-gu", config.guest_username, "-gp", config.guest_password])
    command.extend(args)
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        tolerated_errors = tolerated_errors or []
        lowered = message.lower()
        if any(token.lower() in lowered for token in tolerated_errors):
            return completed.stdout.strip()
        raise VmAutomationError(message)
    return completed.stdout.strip()


def _build_guest_script(word_macro_name: str, use_tex_toggle: bool) -> Path:
    default_candidates = DEFAULT_TEX_TOGGLE_CANDIDATES if use_tex_toggle else DEFAULT_CONVERT_EQNS_CANDIDATES
    candidates = [word_macro_name] if word_macro_name.strip() else default_candidates
    candidate_lines = "\n".join(f'    "{name}"' for name in candidates if name.strip())
    word_visible = "$true" if use_tex_toggle else "$false"
    mode_block = _build_tex_toggle_block() if use_tex_toggle else _build_convert_eqns_block()
    script = """
param(
    [string]$InputPath,
    [string]$OutputPath,
    [string]$LogPath
)
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
function Write-Log($message) {
    try {
        Add-Content -Path $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message) -Encoding UTF8
    } catch {
    }
}
function Start-DialogAutoConfirm($processId) {
    $helperScript = @"
Start-Sleep -Milliseconds 1800
$ws = New-Object -ComObject WScript.Shell
for ($i = 0; $i -lt 12; $i++) {
    try {
        [void]$ws.AppActivate($processId)
        Start-Sleep -Milliseconds 300
        $ws.SendKeys('{ENTER}')
    } catch {
    }
    Start-Sleep -Milliseconds 1800
}
"@
    Start-Process 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-Command',
        $helperScript
    ) | Out-Null
}
function Ensure-UIAutomationAssemblies() {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
}
function Get-WordWindowElement($processId) {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
        $processId
    )
    return $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
}
function Find-WordUiElement($rootElement, $nameRegex, $typeRegex) {
    if ($null -eq $rootElement) {
        return $null
    }
    $all = $rootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    for ($i = 0; $i -lt $all.Count; $i++) {
        $element = $all.Item($i)
        $name = $element.Current.Name
        $type = $element.Current.ControlType.ProgrammaticName
        if ($name -match $nameRegex -and $type -match $typeRegex) {
            return $element
        }
    }
    return $null
}
function Invoke-WordUiElement($element) {
    if ($null -eq $element) {
        return $false
    }
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
        return $true
    } catch {
    }
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
        $pattern.Select()
        return $true
    } catch {
    }
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        $pattern.DoDefaultAction()
        return $true
    } catch {
    }
    return $false
}
function Invoke-WordRibbonTeXToggle($processId) {
    Ensure-UIAutomationAssemblies
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $window = Get-WordWindowElement $processId
        if ($null -eq $window) {
            Start-Sleep -Milliseconds 800
            continue
        }
        $tab = Find-WordUiElement $window '^MathType$' 'TabItem'
        if ($tab -ne $null) {
            if (Invoke-WordUiElement $tab) {
                Write-Log "ui-tab-invoked: MathType"
                Start-Sleep -Milliseconds 800
            }
        }
        $button = Find-WordUiElement $window '切换 ?TeX|TeX' 'Button|SplitButton|MenuItem'
        if ($button -ne $null) {
            Write-Log ("ui-button-found: " + $button.Current.Name)
            if (Invoke-WordUiElement $button) {
                Write-Log ("ui-button-invoked: " + $button.Current.Name)
                return $true
            }
            Write-Log ("ui-button-invoke-failed: " + $button.Current.Name)
        }
        Start-Sleep -Milliseconds 1000
    }
    return $false
}
function Get-RemainingTeXCount($text) {
    $dollarMatches = [regex]::Matches($text, '(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')
    $parenMatches = [regex]::Matches($text, '\\\((.+?)\\\)')
    return $dollarMatches.Count + $parenMatches.Count
}
function Get-MathTypeOleCount($doc) {
    $count = 0
    foreach ($shape in $doc.InlineShapes) {
        try {
            if ($shape.OLEFormat.ProgID -eq 'Equation.DSMT4') {
                $count += 1
            }
        } catch {
        }
    }
    return $count
}
function Convert-TeXSelectionsViaUi($word, $doc, $processId) {
    $convertedCount = 0
    for ($iteration = 0; $iteration -lt 80; $iteration++) {
        $beforeText = $doc.Content.Text
        $match = [regex]::Match($beforeText, '(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')
        if (-not $match.Success) {
            $match = [regex]::Match($beforeText, '\\\((.+?)\\\)')
        }
        if (-not $match.Success) {
            Write-Log ("ui-no-more-tex; converted=" + $convertedCount)
            return $convertedCount
        }
        $range = $doc.Range($match.Index, $match.Index + $match.Length)
        $range.Select()
        Write-Log ("ui-range-selected: " + $match.Value)
        if ($processId -ne $null) {
            Write-Log ("dialog-helper-refreshed-for-pid: " + $processId)
            Start-DialogAutoConfirm $processId
        }
        if (-not (Invoke-WordRibbonTeXToggle $processId)) {
            Write-Log "ui-range-toggle-failed"
            return $convertedCount
        }
        Start-Sleep -Milliseconds 1800
        $afterText = $doc.Content.Text
        if ($afterText -eq $beforeText) {
            Write-Log "ui-range-toggle-nochange"
            return $convertedCount
        }
        $convertedCount += 1
    }
    Write-Log ("ui-iteration-limit; converted=" + $convertedCount)
    return $convertedCount
}
try {
    Write-Log "script-start"
    $word = New-Object -ComObject Word.Application
    Write-Log "word-created"
    $word.Visible = __WORD_VISIBLE__
    $word.DisplayAlerts = 0
    $wordProcess = Get-Process WINWORD -ErrorAction SilentlyContinue | Sort-Object StartTime -Descending | Select-Object -First 1
    if ($wordProcess -ne $null) {
        Write-Log ("dialog-helper-started-for-pid: " + $wordProcess.Id)
        Start-DialogAutoConfirm $wordProcess.Id
    }

    $templateCandidates = @(
        'C:\\Program Files (x86)\\MathType\\Office Support\\64\\MathType Commands 2016.dotm',
        'C:\\Program Files (x86)\\MathType\\Office Support\\64\\MathType Commands.dotm',
        'C:\\Program Files (x86)\\MathType\\Office Support\\64\\WordCmds.dot',
        'C:\\Program Files (x86)\\MathType\\Office Support\\32\\MathType Commands 2016.dotm',
        'C:\\Program Files (x86)\\MathType\\Office Support\\32\\MathType Commands.dotm',
        'C:\\Program Files (x86)\\MathType\\Office Support\\32\\WordCmds.dot'
    )

    foreach ($templatePath in $templateCandidates) {
        if (Test-Path $templatePath) {
            try {
                $addin = $word.AddIns.Add($templatePath, $true)
                $addin.Installed = $true
                Write-Log ("addin-loaded: " + $templatePath)
            } catch {
                Write-Log ("addin-load-failed: " + $templatePath + " | " + $_.Exception.Message)
            }
        }
    }

__MODE_BLOCK__

    $doc.SaveAs([ref]$OutputPath, [ref]16)
    Write-Log ("document-saved: " + $OutputPath)
} catch {
    Write-Log ("script-error: " + $_.Exception.Message)
    throw
} finally {
    if ($doc -ne $null) {
        try { $doc.Close([ref]$false) } catch {}
    }
    if ($word -ne $null) {
        try { $word.Quit() } catch {}
    }
    Write-Log "script-end"
}
"""
    script = script.replace("__WORD_VISIBLE__", word_visible)
    script = script.replace("__MODE_BLOCK__", mode_block)
    script = script.replace("__MACRO_CANDIDATES__", candidate_lines)
    fd, path = tempfile.mkstemp(prefix="problem_manager_vmware_", suffix=".ps1")
    os.close(fd)
    script_path = Path(path)
    script_path.write_text(script.strip() + "\n", encoding="utf-8-sig")
    return script_path


def _build_convert_eqns_block() -> str:
    return textwrap.dedent(
        """
        $doc = $word.Documents.Open($InputPath)
        Write-Log "document-opened"
        Start-Sleep -Milliseconds 1500
        $wordProcess = Get-Process WINWORD -ErrorAction SilentlyContinue | Sort-Object StartTime -Descending | Select-Object -First 1

        $macroCandidates = @(
        __MACRO_CANDIDATES__
        )
        $macroRan = $false
        foreach ($macroName in $macroCandidates) {
            if (-not [string]::IsNullOrWhiteSpace($macroName)) {
                try {
                    Write-Log ("macro-trying: " + $macroName)
                    if ($macroName -like '*ConvertEqns*' -and $wordProcess -ne $null) {
                        Write-Log ("dialog-helper-refreshed-for-pid: " + $wordProcess.Id)
                        Start-DialogAutoConfirm $wordProcess.Id
                    }
                    $word.Run($macroName)
                    Start-Sleep -Milliseconds 2500
                    $macroRan = $true
                    Write-Log ("macro-ran: " + $macroName)
                    break
                } catch {
                    Write-Log ("macro-failed: " + $macroName + " | " + $_.Exception.Message)
                }
            }
        }

        if (-not $macroRan) {
            throw "No executable MathType Word macro was found."
        }
        """
    ).strip()


def _build_tex_toggle_block() -> str:
    return textwrap.dedent(
        """
        $doc = $word.Documents.Open($InputPath)
        Write-Log "document-opened"
        Start-Sleep -Milliseconds 1500
        $word.Activate()
        $wordProcess = Get-Process WINWORD -ErrorAction SilentlyContinue | Sort-Object StartTime -Descending | Select-Object -First 1
        $macroRan = $false

        if ($wordProcess -ne $null) {
            try {
                $initialRemaining = Get-RemainingTeXCount $doc.Content.Text
                $initialOleCount = Get-MathTypeOleCount $doc
                if ($initialRemaining -gt 1) {
                    $word.ActiveDocument.Content.Select()
                    Write-Log "document-selected"
                    Write-Log ("dialog-helper-refreshed-for-pid: " + $wordProcess.Id)
                    Start-DialogAutoConfirm $wordProcess.Id
                    if (Invoke-WordRibbonTeXToggle $wordProcess.Id) {
                        Start-Sleep -Milliseconds 2200
                        $afterRemaining = Get-RemainingTeXCount $doc.Content.Text
                        $afterOleCount = Get-MathTypeOleCount $doc
                        Write-Log ("document-toggle-stats: remaining=" + $afterRemaining + "; ole=" + $afterOleCount)
                        if ($afterRemaining -eq 0 -and $afterOleCount -gt $initialOleCount) {
                            $macroRan = $true
                            Write-Log "ui-document-ran: 切换 TeX"
                        } elseif ($afterRemaining -eq 0 -and $afterOleCount -eq $initialOleCount) {
                            try {
                                $word.Undo()
                                Start-Sleep -Milliseconds 1000
                                Write-Log "ui-document-undone"
                            } catch {
                                Write-Log ("ui-document-undo-failed: " + $_.Exception.Message)
                            }
                        }
                    }
                }
            } catch {
                Write-Log ("ui-document-failed: " + $_.Exception.Message)
            }
        }

        if (-not $macroRan -and $wordProcess -ne $null) {
            try {
                $word.Activate()
                $convertedCount = Convert-TeXSelectionsViaUi $word $doc $wordProcess.Id
                if ($convertedCount -gt 0) {
                    $macroRan = $true
                    Write-Log ("ui-button-ran: 切换 TeX; converted=" + $convertedCount)
                } else {
                    Write-Log "ui-button-not-found-or-nochange: 切换 TeX"
                }
            } catch {
                Write-Log ("ui-button-failed: " + $_.Exception.Message)
            }
        }

        if (-not $macroRan) {
            $macroCandidates = @(
            __MACRO_CANDIDATES__
            )
            foreach ($macroName in $macroCandidates) {
                if (-not [string]::IsNullOrWhiteSpace($macroName)) {
                    try {
                        Write-Log ("macro-trying: " + $macroName)
                        try {
                            $word.ActiveDocument.Content.Select()
                        } catch {
                        }
                        if ($wordProcess -ne $null) {
                            Write-Log ("dialog-helper-refreshed-for-pid: " + $wordProcess.Id)
                            Start-DialogAutoConfirm $wordProcess.Id
                        }
                        $word.Run($macroName)
                        Start-Sleep -Milliseconds 4000
                        $macroRan = $true
                        Write-Log ("macro-ran: " + $macroName)
                        break
                    } catch {
                        Write-Log ("macro-failed: " + $macroName + " | " + $_.Exception.Message)
                    }
                }
            }
        }

        if (-not $macroRan) {
            throw "No executable MathType TeX toggle entry was found."
        }
        """
    ).strip()


def _guest_join(directory: str, filename: str) -> str:
    return directory.rstrip("\\/") + "\\" + filename


def _wait_for_tools(vmx_path: str, config: VmAutomationConfig) -> None:
    deadline = time.time() + 120
    last_message = "VMware Tools 未就绪。"
    while time.time() < deadline:
        try:
            output = _run_vmrun(["checkToolsState", vmx_path], config, guest_auth=False)
            if "running" in output.lower() or "installed" in output.lower():
                return
            last_message = output or last_message
        except VmAutomationError as error:
            last_message = str(error)
        time.sleep(3)
    raise VmAutomationError(f"等待 VMware Tools 就绪超时：{last_message}")


def _wait_for_guest_file(vmx_path: str, guest_path: str, config: VmAutomationConfig) -> None:
    deadline = time.time() + 240
    while time.time() < deadline:
        completed = subprocess.run(
            [
                VMRUN_PATH,
                "-T",
                "fusion",
                "-vp",
                config.vm_password,
                "-gu",
                config.guest_username,
                "-gp",
                config.guest_password,
                "fileExistsInGuest",
                vmx_path,
                guest_path,
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(3)
    raise VmAutomationError(f"等待 Win11 输出文件超时：{guest_path}")
