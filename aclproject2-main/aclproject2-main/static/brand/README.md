# Supplied KneeROM login marks

patient-logo.png and doctor-logo.png are the original PNG files supplied by the project owner for this login preview. The marks have not been redrawn or recolored; CSS frames the existing white padding for responsive display.

Source folder: https://drive.google.com/drive/folders/1u_goGpfkvJhi9kdhSiqiQoZozg86S2tI

patient-background.png and doctor-background.png are also the original supplied PNGs from that folder. CSS applies blur, desaturation, opacity and a central mask to the decorative background layer only. No raster files have been edited. The login form remains sharp; switching role crossfades the two backgrounds, respecting reduced-motion and reduced-transparency preferences.

The doctor background is displayed through an inline SVG mask in home.html. Its cutout is defined in the source image's coordinates, so the large badge printed into that background stays hidden at every screen size. The independent foreground doctor-logo.png and medical pattern remain visible.
