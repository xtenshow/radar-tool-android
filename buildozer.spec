[app]
title = Radar Tool
package.name = radartool
package.domain = org.tenshorecords
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf
version = 1.0.0

requirements = python3,kivy==2.3.1,kivymd==2.0.0,pillow,plyer,pyjnius,materialyoucolor==3.0.3,materialshapes,pycairo,exceptiongroup,asyncgui,asynckivy,android

orientation = portrait
fullscreen = 0

# Android Storage Access Framework is used; broad storage permissions are not needed.
android.api = 33
android.minapi = 26
android.ndk = 25b
android.accept_sdk_license = True
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 0
