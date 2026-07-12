// HyperSpace Intent Protocol (HIP) Schema
// Production-ready TypeScript definitions for 1.04

export interface HIPIntent {
  id: string;
  type: 'task' | 'query' | 'command' | 'negotiation' | 'capability';
  payload: any;
  metadata: {
    source: string;
    timestamp: string;
    priority: number;
    ttl?: number;
    capabilitiesRequired?: string[];
  };
  context?: {
    userId?: string;
    sessionId?: string;
    tenantId?: string; // for Enterprise Local
  };
}

export interface Capability {
  id: string;
  name: string;
  description: string;
  version: string;
  supportedIntents: string[];
  resourceLimits: {
    cpu?: number;
    memory?: number;
    gpu?: boolean;
  };
}

export interface WorkerNegotiation {
  intentId: string;
  workerId: string;
  bid: {
    estimatedTime: number;
    confidence: number;
    cost?: number;
  };
  accepted: boolean;
}

// Intent Router interface
export interface IntentRouter {
  route(intent: HIPIntent): Promise<{ workerId: string; result: any }>;
  advertiseCapability(cap: Capability): Promise<void>;
  negotiate(intentId: string, workers: string[]): Promise<string>;
}
