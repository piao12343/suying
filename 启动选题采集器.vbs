Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
Root = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = Root
Cmd = "cmd /c pythonw """ & Root & "\story_collector_launcher.py"" > """ & Root & "\story_collector_launcher.log"" 2>&1"
WshShell.Run Cmd, 0, False
