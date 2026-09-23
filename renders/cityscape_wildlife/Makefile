CC ?= gcc
CFLAGS ?= -O3 -march=native -ffast-math -fno-finite-math-only -fopenmp -Wall -Wextra -Wno-unused-parameter
LDLIBS = -lm -lz

render: render.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

# Final image: 1920x1080, 256 samples per pixel (about an hour on 4 cores); keeps the linear HDR frame
image: render
	./render -w 1920 -h 1080 -spp 256 -raw cityscape_wildlife.raw -o cityscape_wildlife.png

# Watercolour version, repainted from the saved HDR frame (a few seconds)
watercolour: render
	./render -develop cityscape_wildlife.raw -style watercolour -o cityscape_wildlife_watercolor.png

preview: render
	./render -w 640 -h 360 -spp 16 -o preview.png

clean:
	rm -f render preview.png

.PHONY: image watercolour preview clean
