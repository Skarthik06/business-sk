' Business-SK scraper watchdog (laptop). Runs every 5 min from Task Scheduler ("BusinessSK scraper
' watchdog"): if scripts\scrape_worker.py isn't running, start it (hidden). Never starts a 2nd copy.
Option Explicit
Dim repo, py, wmi, procs, p, found, sh
repo = "C:\Users\PRATHEEK S\BUSINESS_SK"
py = "C:\Users\PRATHEEK S\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
found = False
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name='pythonw.exe' OR Name='python.exe'")
For Each p In procs
  If Not IsNull(p.CommandLine) Then
    If InStr(LCase(p.CommandLine), "scrape_worker.py") > 0 Then found = True
  End If
Next
If Not found Then
  Set sh = CreateObject("WScript.Shell")
  sh.CurrentDirectory = repo
  sh.Run """" & py & """ scripts\scrape_worker.py", 0, False
End If
