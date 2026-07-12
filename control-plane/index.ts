import HyperSpaceIntentRouter from './src/intent-router.js';
import { HIPIntent, Capability } from '@hyperspace/shared';

console.log('HyperSpace Control Plane 1.04 starting...');

// Example usage
const router = new HyperSpaceIntentRouter({ register: async (c) => console.log('Registered', c) });

const exampleCap: Capability = {
  id: 'msty-claw-1',
  name: 'Msty Claw Runtime',
  description: 'Default autonomous runtime',
  version: '1.0',
  supportedIntents: ['task', 'query'],
  resourceLimits: { cpu: 4, memory: 8192, gpu: true }
};

router.advertiseCapability(exampleCap).then(() => {
  const intent: HIPIntent = {
    id: 'intent-001',
    type: 'task',
    payload: { action: 'summarize' },
    metadata: {
      source: 'msty-studio',
      timestamp: new Date().toISOString(),
      priority: 5
    }
  };
  return router.route(intent);
}).then(result => {
  console.log('[Control Plane] Execution result:', result);
});
