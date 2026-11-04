"""One bounded local inference. Structured facts in stdin; final text only on stdout."""
import json
import os
from pathlib import Path
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    root=Path(os.getenv('ACL_AI_ROOT',str(Path(__file__).resolve().parent.parent.parent)))
    runtime=Path(os.getenv('ACL_AI_RUNTIME',str(root/'ai_runtime')))
    model=Path(os.getenv('ACL_LLM_MODEL',str(root/'models'/'Qwen3-0.6B.litertlm')))
    if not model.is_file(): raise FileNotFoundError('Local LLM model unavailable')
    sys.path.insert(0,str(runtime))
    import litert_lm
    litert_lm.set_min_log_severity(litert_lm.LogSeverity.ERROR)
    facts=json.loads(sys.stdin.read(12000))
    lines=[]
    for source,item in facts.get('sources',{}).items():
        if not item.get('count'):
            lines.append(source+': chưa có dữ liệu.')
        else:
            lines.append(f"{source}: {item['count']} lượt đo; lần đầu {item['earliest_deg']} độ; "
                         f"gần nhất {item['latest_deg']} độ; cao nhất {item['maximum_deg']} độ; trung bình {item['mean_deg']} độ.")
    prompt=('Tóm tắt dữ liệu sau cho bác sĩ bằng 2-4 câu tiếng Việt. Viết văn bản thường, '
            'không viết JSON, không dùng khối mã. Chỉ mô tả số liệu, không chẩn đoán hay khuyên điều trị.\n'
            +'\n'.join(lines)+'\nBản tóm tắt tiếng Việt:')
    cache=root/'ai_runtime'/'cache'
    cache.mkdir(parents=True,exist_ok=True)
    with litert_lm.Engine(str(model),backend=litert_lm.Backend.CPU(thread_count=2),max_num_tokens=1536,cache_dir=str(cache)) as engine:
        with engine.create_conversation(
            messages=[litert_lm.Message.system('Bạn là trợ lý tóm tắt số liệu. Viết ngắn gọn bằng tiếng Việt, giữ nguyên số liệu được cung cấp.')],
            thinking_config=litert_lm.ThinkingConfig(enable_thinking=False,thinking_token_budget=0),
            max_output_tokens=240,
        ) as conversation:
            response=conversation.send_message(prompt)
            text=''.join(item.get('text','') for item in response.get('content',[]) if item.get('type')=='text')
    if not text.strip(): raise RuntimeError('Model returned no summary')
    print(json.dumps({'text':text.strip(),'model':model.name,'runtime':'LiteRT-LM'},ensure_ascii=False))


if __name__=='__main__':main()
