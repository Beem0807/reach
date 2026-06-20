interface Props {
  agentId: string;
  hostname?: string | null;
}

export function AgentInfo({ agentId, hostname }: Props) {
  return (
    <div>
      <p className="font-medium text-gray-800 text-xs">{hostname ?? agentId}</p>
      {hostname && (
        <p className="font-mono text-[10px] text-gray-400 mt-0.5 truncate max-w-[160px]">{agentId}</p>
      )}
    </div>
  );
}
