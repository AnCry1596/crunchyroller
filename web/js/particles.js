// soft floating particles on a dark background
// nothing fancy, just some dim dots drifting around

const canvas = document.createElement('canvas');
canvas.id = 'particles';
canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:-1;pointer-events:none';
document.body.prepend(canvas);

const ctx = canvas.getContext('2d');
let w, h;
const dots = [];
const COUNT = 35;

function resize() {
  w = canvas.width = window.innerWidth;
  h = canvas.height = window.innerHeight;
}

function init() {
  resize();
  for (let i = 0; i < COUNT; i++) {
    dots.push({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 1.5 + 0.5,
      dx: (Math.random() - 0.5) * 0.15,
      dy: (Math.random() - 0.5) * 0.15,
      opacity: Math.random() * 0.25 + 0.05,
      pulse: Math.random() * Math.PI * 2,
    });
  }
}

function draw() {
  ctx.clearRect(0, 0, w, h);

  for (const d of dots) {
    // slow drift
    d.x += d.dx;
    d.y += d.dy;

    // wrap around edges
    if (d.x < -10) d.x = w + 10;
    if (d.x > w + 10) d.x = -10;
    if (d.y < -10) d.y = h + 10;
    if (d.y > h + 10) d.y = -10;

    // gentle breathing effect
    d.pulse += 0.008;
    const alpha = d.opacity + Math.sin(d.pulse) * 0.06;

    // the glow
    ctx.beginPath();
    ctx.arc(d.x, d.y, d.r + 4, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255, 255, 255, ${alpha * 0.15})`;
    ctx.fill();

    // the dot
    ctx.beginPath();
    ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255, 255, 255, ${alpha})`;
    ctx.fill();
  }

  requestAnimationFrame(draw);
}

window.addEventListener('resize', resize);
init();
draw();
