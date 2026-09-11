"""Exercise game data-safety behavior in a JavaScript VM without rendering 3D."""
import json
import subprocess
import unittest

import test_web_render as rendering


class GameBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the isolated application fixture, never the real database.
        cls.fixture = type("GameFixture", (rendering.WebRenderTests,), {})
        cls.fixture.setUpClass()
        cls.addClassCleanup(cls.fixture.doClassCleanups)

    def test_demo_is_not_saved_and_real_source_is_locked(self):
        fixture = self.fixture
        if not fixture.node:
            self.skipTest("Node.js is required for game behavior checks")
        client = fixture.module.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=1, role="patient", name="Test")
        parser = rendering.Scripts()
        parser.feed(client.get("/game").get_data(as_text=True))
        source = "\n;\n".join(parser.scripts)
        harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
function context() {
  const nodes = new Map();
  const requests = [];
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {style: {}, disabled: false, classList: {add(){}, remove(){}}});
    return nodes.get(id);
  }
  const sandbox = {
    console, performance: {now: () => 0}, Date,
    window: {addEventListener(){}},
    document: {addEventListener(){}, getElementById: node, querySelectorAll: () => []},
    setTimeout: () => 0, requestAnimationFrame() {},
    Rehab: {
      poll() {}, notify() {},
      api: async (url, options={}) => {
        requests.push({url, options});
        return {success: true, rom: 70, angle: 80, receiving: true};
      }
    }
  };
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return {sandbox, node, requests, run: code => vm.runInContext(code, sandbox)};
}
(async () => {
  const demo = context();
  demo.run("renderer={}; startGame(); best=120; running=false; gameover();");
  assert.equal(demo.node('saveBtn').disabled, true);
  await demo.run("saveResult()");
  assert.equal(demo.requests.length, 0);

  const real = context();
  real.run("src='imu'; renderer={}; startGame(); setSrc('cam',{}); toggleAuto();");
  assert.equal(real.run("src"), 'imu');
  assert.equal(real.run("auto"), false);
  real.run("best=70; shots=SHOTS_NEED; running=false; gameover();");
  await real.run("pollReal({aborted:false})");
  assert.equal(real.run("signalReady"), true);
  assert.equal(real.run("rawAngle"), 80);
  await real.run("saveResult()");
  const saved = real.requests.find(r => r.url === '/game_save');
  assert.deepEqual(JSON.parse(saved.options.body), {rom:70, source:'imu'});
  const count = real.requests.length;
  await real.run("saveResult()");
  assert.equal(real.requests.length, count);
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        program = "const SOURCE=" + json.dumps(source) + ";\n" + harness
        result = subprocess.run([fixture.node], input=program, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()