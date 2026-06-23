Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\Personal\Desktop\suying-github"
WshShell.Run "pythonw 源码\tools\story_collector.py", 0, False
