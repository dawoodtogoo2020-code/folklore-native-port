package org.folklore.port;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/** Human touch state only. No timers, generated gestures or motion samples. */
final class FolkloreTouchState {
    interface Sink { void button(int button, boolean down); void axis(int axis, float value); }
    static final int BUTTON = 0, STICK = 1, TRIGGER = 2;
    static final class Control {
        final String label;
        final int kind, index;
        final float x, y, radius;
        Control(String label, int kind, int index, float x, float y, float radius) {
            this.label=label; this.kind=kind; this.index=index;
            this.x=x; this.y=y; this.radius=radius;
        }
        boolean contains(float px, float py) {
            float dx=px-x, dy=py-y;
            return finite(px) && finite(py) && dx*dx+dy*dy<=radius*radius;
        }
    }
    private static final class Hold {
        final Control control;
        boolean active=true;
        float x, y;
        Hold(Control c) { control=c; }
    }
    private final Sink sink;
    private final Map<Integer,Hold> holds=new HashMap<Integer,Hold>();
    private final boolean[] buttons=new boolean[15];
    private final float[] axes={0,0,0,0,-1,-1};
    private Control[] controls=new Control[0];
    FolkloreTouchState(Sink sink) { if(sink==null)throw new IllegalArgumentException("sink"); this.sink=sink; }
    static boolean finite(float f) { return !Float.isNaN(f) && !Float.isInfinite(f); }
    Control[] controls() { return controls.clone(); }
    float axis(int i) { return axes[i]; }
    boolean pressed(Control c) {
        if(c.kind==BUTTON)return buttons[c.index];
        if(c.kind==TRIGGER)return axes[c.index]>0;
        for(Hold h:holds.values())if(h.control==c)return true;
        return false;
    }
    void setSize(float width,float height) {
        cancelAll();
        if(!finite(width)||!finite(height)||width<=0||height<=0) { controls=new Control[0];return; }
        float scale=Math.min(width/900f,height/450f);
        float ox=(width-900*scale)/2, oy=(height-450*scale)/2;
        String[] labels={"L","R","L1","L2","R1","R2","L3","R3","Select","Start","Up","Down","Left","Right","Cross","Circle","Square","Triangle"};
        int[] kind={STICK,STICK,BUTTON,TRIGGER,BUTTON,TRIGGER,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON,BUTTON};
        int[] index={0,2,9,4,10,5,7,8,4,6,11,12,13,14,0,1,2,3};
        float[] xs={125,625,70,180,720,830,125,625,410,490,285,285,237,333,800,848,752,800};
        float[] ys={325,325,70,70,70,70,220,220,385,385,232,328,280,280,328,280,280,232};
        float[] rs={70,65,30,30,30,30,24,24,26,26,25,25,25,25,28,28,28,28};
        controls=new Control[labels.length];
        for(int i=0;i<controls.length;i++)controls[i]=new Control(labels[i],kind[i],index[i],ox+xs[i]*scale,oy+ys[i]*scale,rs[i]*scale);
    }
    void pointerDown(int id,float x,float y) {
        if(id<0||holds.containsKey(id)||!finite(x)||!finite(y))return;
        for(Control c:controls)if(c.contains(x,y)) {
            if(c.kind==STICK)for(Hold other:holds.values())if(other.control==c)return;
            Hold h=new Hold(c); holds.put(id,h); move(h,x,y); publish(false);return;
        }
    }
    void pointerMove(int id,float x,float y) {
        Hold h=holds.get(id); if(h==null)return;
        move(h,x,y);publish(false);
    }
    private void move(Hold h,float x,float y) {
        Control c=h.control;
        if(c.kind!=STICK) {h.active=c.contains(x,y);return;}
        if(!finite(x)||!finite(y)) {h.x=h.y=0;return;}
        double dx=((double)x-c.x)/c.radius,dy=((double)y-c.y)/c.radius;
        double length=Math.hypot(dx,dy);
        if(length<=.12) {h.x=h.y=0;return;}
        double strength=(Math.min(length,1)-.12)/.88;
        h.x=(float)(dx/length*strength);h.y=(float)(dy/length*strength);
    }
    void pointerUp(int id) { if(holds.remove(id)!=null)publish(false); }
    void retainPointers(int[] ids) {
        Iterator<Map.Entry<Integer,Hold>> it=holds.entrySet().iterator();
        while(it.hasNext()) {
            int id=it.next().getKey();boolean present=false;
            for(int candidate:ids)if(candidate==id)present=true;
            if(!present)it.remove();
        }
        publish(false);
    }
    void cancelAll() {holds.clear();publish(false);}
    void forceNeutral() {holds.clear();publish(true);}
    private void publish(boolean force) {
        boolean[] nextButtons=new boolean[15];float[] nextAxes={0,0,0,0,-1,-1};
        for(Hold h:holds.values()) {
            Control c=h.control;
            if(c.kind==STICK) {nextAxes[c.index]=h.x;nextAxes[c.index+1]=h.y;}
            else if(h.active) {if(c.kind==BUTTON)nextButtons[c.index]=true;else nextAxes[c.index]=1;}
        }
        for(int i=0;i<buttons.length;i++)if(force||buttons[i]!=nextButtons[i]) {
            buttons[i]=nextButtons[i];sink.button(i,buttons[i]);
        }
        for(int i=0;i<axes.length;i++)if(force||axes[i]!=nextAxes[i]) {
            axes[i]=nextAxes[i];sink.axis(i,axes[i]);
        }
    }
}
