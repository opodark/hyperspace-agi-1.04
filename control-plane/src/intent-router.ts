// Intent Router for HyperSpace Control Plane - 1.04
import { HIPIntent, IntentRouter, Capability, WorkerNegotiation } from '@hyperspace/shared';

export class HyperSpaceIntentRouter implements IntentRouter {
  private workers: Map<string, Capability> = new Map();
  private registry: any; // Injected registry client

  constructor(registry: any) {
    this.registry = registry;
  }

  async advertiseCapability(cap: Capability): Promise<void> {
    this.workers.set(cap.id, cap);
    console.log(`[HIP] Capability advertised: ${cap.name} by ${cap.id}`);
    // Persist to registry
    await this.registry.register(cap);
  }

  async route(intent: HIPIntent): Promise<{ workerId: string; result: any }> {
    console.log(`[HIP] Routing intent ${intent.id} of type ${intent.type}`);
    
    // Simple routing logic - find best match
    const suitableWorkers = Array.from(this.workers.values())
      .filter(w => w.supportedIntents.includes(intent.type));
    
    if (suitableWorkers.length === 0) {
      throw new Error('No suitable worker found');
    }

    // For now, pick first; later use negotiation
    const selected = suitableWorkers[0];
    console.log(`[HIP] Routed to worker ${selected.id}`);

    // Simulate execution or delegate
    const result = { status: 'executed', output: `Processed ${intent.type}` };
    return { workerId: selected.id, result };
  }

  async negotiate(intentId: string, workers: string[]): Promise<string> {
    console.log(`[HIP] Negotiating for intent ${intentId}`);
    // TODO: Implement bidding
    return workers[0]; // placeholder
  }
}

export default HyperSpaceIntentRouter;
