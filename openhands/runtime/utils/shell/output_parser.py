def extract_between_prompts(pane_output: str, prompts):
    """Extract command output between prompt A and prompt B."""
    if not prompts:
        return pane_output

    if len(prompts) == 1:
        return pane_output[prompts[0].end() + 1 :]

    out = []
    for i in range(len(prompts) - 1):
        seg = pane_output[prompts[i].end() + 1 : prompts[i+1].start()]
        out.append(seg)
    out.append(pane_output[prompts[-1].end() + 1 :])
    return "\n".join(out)
