Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\ingam\Desktop\trad"
WshShell.Run """C:\Users\ingam\Desktop\trad\venv\Scripts\pythonw.exe"" ""C:\Users\ingam\Desktop\trad\ocr_translate_popup_windows.py""", 0, False
