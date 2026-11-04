# Supplied KneeROM login marks

patient-logo.png and doctor-logo.png are the original PNG files supplied by the project owner for this login preview. The marks have not been redrawn or recolored; CSS frames the existing white padding for responsive display.

Source folder: https://drive.google.com/drive/folders/1u_goGpfkvJhi9kdhSiqiQoZozg86S2tI

doctor-white.svg renders the exact white monogram from doctor-logo.png using an embedded copy of that PNG, a native SVG color/alpha filter and a tightly scoped viewBox. This removes the cyan badge while preserving the supplied geometry. The original PNG remains unchanged. A CSS shadow provides contrast on the light login background.

patient-background.png and doctor-background.png are also the original supplied PNGs from that folder. CSS applies blur, desaturation, opacity and a central mask to the decorative background layer only. No raster files have been edited. The login form remains sharp; switching role crossfades the two backgrounds, respecting reduced-motion and reduced-transparency preferences.
