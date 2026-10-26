three.js r160 (MIT, see LICENSE), from https://cdn.jsdelivr.net/npm/three@0.160.0/
- three.module.min.js  build/three.module.min.js         sha256 3e690ac7d180b0aadf0891bea39eec643e29e2d3e75c99b18689518665f69ba6
- OrbitControls.js     examples/jsm/controls/OrbitControls.js, with its one `from 'three'` import
                       pointed at ./three.module.min.js so no import map is needed
Bundled, like Leaflet, so the Studio needs no outside script host and works offline in the field.
