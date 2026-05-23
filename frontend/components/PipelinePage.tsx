import React, { useEffect, useState, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Edge,
  Node,
  NodeProps,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { DashboardStats, Trade } from '@/lib/types';

// --- CUSTOM NODE ---

type PipelineNodeData = {
  label: string;
  icon?: React.ReactNode;
  value?: string | number;
  subValue?: string;
  isActive?: boolean;
  isProcessing?: boolean;
};

function PipelineNode({ data, isConnectable }: NodeProps<Node<PipelineNodeData>>) {
  return (
    <div
      className={`relative min-w-[200px] rounded-2xl border border-[var(--dashboard-border)] p-4 shadow-[var(--dashboard-shadow)] backdrop-blur-md transition-all duration-300 ${
        data.isActive
          ? 'border-accent bg-accent/10 shadow-[0_0_20px_rgba(var(--accent-rgb),0.2)]'
          : 'bg-[var(--dashboard-card)]'
      }`}
    >
      {/* Top Handle */}
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={isConnectable}
        className="h-2 w-2 border-none bg-muted"
      />

      <div className="flex items-center gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${
            data.isActive
              ? 'border-accent-border bg-accent text-white'
              : 'border-[var(--dashboard-border)] bg-[var(--dashboard-control)] text-[var(--dashboard-text)]'
          } transition-colors duration-300`}
        >
          {data.icon || (
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="2" y="2" width="20" height="20" rx="5" ry="5" />
            </svg>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-[var(--dashboard-text)]">{data.label}</p>
          {data.value !== undefined && (
            <p className="mt-0.5 font-mono text-xs font-semibold text-accent">
              {data.value}{' '}
              <span className="text-[10px] font-normal text-[var(--dashboard-subtle)]">
                {data.subValue}
              </span>
            </p>
          )}
        </div>
      </div>

      {data.isProcessing && (
        <div className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75"></span>
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent"></span>
        </div>
      )}

      {/* Bottom Handle */}
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={isConnectable}
        className="h-2 w-2 border-none bg-muted"
      />
    </div>
  );
}

const nodeTypes = {
  custom: PipelineNode,
};

// --- INITIAL LAYOUT ---

const initialNodes: Node<PipelineNodeData>[] = [
  {
    id: 'ingestion',
    type: 'custom',
    position: { x: 350, y: 50 },
    data: {
      label: 'Data Ingestion',
      subValue: 'News & Events',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8l-4 4v14a2 2 0 0 0 2 2z" />
          <path d="M14 2v4a2 2 0 0 0 2 2h4" />
        </svg>
      ),
    },
  },
  {
    id: 'stream',
    type: 'custom',
    position: { x: 350, y: 180 },
    data: {
      label: 'Event Stream',
      subValue: 'Valkey / Redis',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      ),
    },
  },
  {
    id: 'agent',
    type: 'custom',
    position: { x: 350, y: 310 },
    data: {
      label: 'Agent Analysis',
      subValue: 'Articles analyzed',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <path d="M12 16v-4" />
          <path d="M12 8h.01" />
        </svg>
      ),
    },
  },
  {
    id: 'risk',
    type: 'custom',
    position: { x: 350, y: 440 },
    data: {
      label: 'Risk Gate',
      subValue: 'Trades blocked',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
      ),
    },
  },
  {
    id: 'trader',
    type: 'custom',
    position: { x: 150, y: 570 },
    data: {
      label: 'Alpaca Trader',
      subValue: 'Orders executed',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="12" y1="2" x2="12" y2="22" />
          <line x1="17" y1="5" x2="7" y2="5" />
          <line x1="17" y1="19" x2="7" y2="19" />
          <polyline points="15 9 9 12 15 15" />
        </svg>
      ),
    },
  },
  {
    id: 'database',
    type: 'custom',
    position: { x: 550, y: 570 },
    data: {
      label: 'Supabase Log',
      subValue: 'Total records',
      icon: (
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
        </svg>
      ),
    },
  },
];

const initialEdges: Edge[] = [
  { id: 'e-ingestion-stream', source: 'ingestion', target: 'stream', animated: true },
  { id: 'e-stream-agent', source: 'stream', target: 'agent', animated: true },
  { id: 'e-agent-risk', source: 'agent', target: 'risk', animated: true },
  {
    id: 'e-risk-trader',
    source: 'risk',
    target: 'trader',
    animated: true,
    label: 'BUY / SELL',
    labelBgStyle: { fill: 'var(--dashboard-bg)', color: 'var(--dashboard-text)' },
    style: { stroke: 'var(--positive)' },
  },
  {
    id: 'e-risk-database',
    source: 'risk',
    target: 'database',
    animated: true,
    label: 'HOLD',
    labelBgStyle: { fill: 'var(--dashboard-bg)', color: 'var(--dashboard-text)' },
    style: { stroke: 'var(--muted)' },
  },
  { id: 'e-trader-database', source: 'trader', target: 'database', animated: true },
];

export interface PipelinePageProps {
  stats: DashboardStats | null;
  trades: Trade[];
  newIds: Set<string>;
}

export default function PipelinePage({ stats, trades, newIds }: PipelinePageProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // We want to trigger a visual pulse when `newIds` changes
  const [isPulsing, setIsPulsing] = useState(false);
  const [lastTradeType, setLastTradeType] = useState<'BUY' | 'SELL' | 'HOLD' | null>(null);

  useEffect(() => {
    if (newIds.size > 0) {
      setIsPulsing(true);

      // Look at the most recent trade among the new ones
      const recentTrade = trades.find((t) => newIds.has(t.id));
      if (recentTrade) {
        setLastTradeType(recentTrade.trade_action);
      }

      const timer = setTimeout(() => setIsPulsing(false), 2000);
      return () => clearTimeout(timer);
    }
  }, [newIds, trades]);

  useEffect(() => {
    // Update node values based on stats
    setNodes((nds) =>
      nds.map((node) => {
        const newData = { ...node.data };

        // Update stats
        if (node.id === 'agent') newData.value = stats?.analyzed ?? 0;
        if (node.id === 'risk') newData.value = stats?.riskGated ?? 0;
        if (node.id === 'trader') newData.value = stats?.executed ?? 0;
        if (node.id === 'database') newData.value = trades.length;

        // Visual pulsing logic
        newData.isActive = isPulsing;
        newData.isProcessing = isPulsing;

        // Specific routing logic for pulses
        if (isPulsing) {
          if (node.id === 'trader' && lastTradeType === 'HOLD') {
            newData.isActive = false; // Trader doesn't pulse on HOLD
          }
        }

        return { ...node, data: newData };
      }),
    );

    // Update edge animations
    setEdges((eds) =>
      eds.map((edge) => {
        let active = isPulsing;

        // Edge specific logic
        if (edge.id === 'e-risk-trader' && lastTradeType === 'HOLD') {
          active = false;
        }
        if (edge.id === 'e-trader-database' && lastTradeType === 'HOLD') {
          active = false;
        }
        if (edge.id === 'e-risk-database' && lastTradeType !== 'HOLD') {
          active = false;
        }

        return {
          ...edge,
          animated: true,
          style: {
            ...edge.style,
            strokeWidth: active ? 3 : 1.5,
            opacity: active ? 1 : 0.4,
          },
        };
      }),
    );
  }, [stats, trades, isPulsing, lastTradeType, setNodes, setEdges]);

  return (
    <div className="h-full min-h-[640px] w-full rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-bg)] overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        className="bg-transparent"
        minZoom={0.5}
        maxZoom={1.5}
        defaultEdgeOptions={{
          type: 'smoothstep',
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 15,
            height: 15,
            color: 'var(--dashboard-subtle)',
          },
        }}
      >
        <Background gap={16} size={1} color="var(--dashboard-border)" />
        <Controls
          className="border-line bg-surface-2 fill-primary text-primary"
          showInteractive={false}
        />
      </ReactFlow>
    </div>
  );
}
