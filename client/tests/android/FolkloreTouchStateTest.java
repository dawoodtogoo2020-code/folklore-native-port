package org.folklore.port;

public final class FolkloreTouchStateTest {
    private static int checks;
    private static void check(boolean ok) {checks++;if(!ok)throw new AssertionError("check "+checks);}
    private static final class Sink implements FolkloreTouchState.Sink {
        final boolean[] buttons=new boolean[15];final float[] axes=new float[6];int events;
        public void button(int i,boolean down) {buttons[i]=down;events++;}
        public void axis(int i,float value) {check(FolkloreTouchState.finite(value)&&Math.abs(value)<=1);axes[i]=value;events++;}
        void neutral() {for(boolean b:buttons)check(!b);for(int i=0;i<6;i++)check(axes[i]==(i<4?0:-1));}
    }
    private static FolkloreTouchState.Control control(FolkloreTouchState s,String label) {
        for(FolkloreTouchState.Control c:s.controls())if(c.label.equals(label))return c;
        throw new AssertionError(label);
    }
    private static void down(FolkloreTouchState s,int id,FolkloreTouchState.Control c) {s.pointerDown(id,c.x,c.y);}
    public static void main(String[] args) {
        Sink out=new Sink();FolkloreTouchState s=new FolkloreTouchState(out);
        s.forceNeutral();out.neutral();s.setSize(900,450);
        FolkloreTouchState.Control left=control(s,"L"),cross=control(s,"Cross"),r1=control(s,"R1"),l2=control(s,"L2");
        down(s,7,left);s.pointerMove(7,left.x+left.radius,left.y);
        down(s,42,cross);down(s,91,r1);
        check(out.axes[0]==1&&out.axes[1]==0&&out.buttons[0]&&out.buttons[10]);
        down(s,5,cross);s.pointerUp(42);check(out.buttons[0]);s.pointerUp(5);check(!out.buttons[0]);
        down(s,6,left);s.pointerMove(6,left.x-left.radius,left.y);check(out.axes[0]==1);
        s.pointerUp(7);check(out.axes[0]==0);s.pointerUp(6);
        down(s,3,l2);check(out.axes[4]==1);s.pointerMove(3,l2.x+l2.radius*2,l2.y);check(out.axes[4]==-1);
        s.pointerMove(3,l2.x,l2.y);check(out.axes[4]==1);s.pointerUp(3);check(out.axes[4]==-1);
        s.cancelAll();out.neutral();
        down(s,8,left);s.pointerMove(8,left.x+left.radius*4,left.y+left.radius*4);
        check(Math.abs(out.axes[0]-.70710677f)<.00001&&Math.abs(out.axes[1]-.70710677f)<.00001);
        s.pointerMove(8,left.x+left.radius*.1f,left.y);check(out.axes[0]==0);
        s.pointerMove(8,Float.NaN,Float.POSITIVE_INFINITY);check(out.axes[0]==0&&out.axes[1]==0);
        s.pointerMove(8,Float.MAX_VALUE,-Float.MAX_VALUE);check(out.axes[0]>0&&out.axes[1]<0);
        s.retainPointers(new int[0]);out.neutral();
        down(s,1,cross);down(s,2,r1);s.retainPointers(new int[]{2});check(!out.buttons[0]&&out.buttons[10]);
        s.setSize(1800,900);out.neutral();
        int events=out.events;s.pointerDown(5,Float.NaN,0);s.pointerDown(-1,0,0);s.pointerUp(999);check(out.events==events);
        for(float[] size:new float[][]{{900,450},{2772,1272},{1920,1080},{640,480},{450,900}}) {
            s.setSize(size[0],size[1]);FolkloreTouchState.Control[] cs=s.controls();check(cs.length==18);
            for(int i=0;i<cs.length;i++) {
                FolkloreTouchState.Control c=cs[i];check(c.x-c.radius>=0&&c.x+c.radius<=size[0]&&c.y-c.radius>=0&&c.y+c.radius<=size[1]);
                for(int j=0;j<i;j++)check(Math.hypot(c.x-cs[j].x,c.y-cs[j].y)>c.radius+cs[j].radius);
                down(s,i,c);check(s.pressed(c));s.pointerUp(i);out.neutral();
            }
        }
        s.setSize(0,450);check(s.controls().length==0);s.setSize(Float.NaN,450);check(s.controls().length==0);
        s.setSize(900,450);cross=control(s,"Cross");down(s,4,cross);s.pointerDown(4,0,0);check(out.buttons[0]);
        s.pointerMove(4,0,0);check(!out.buttons[0]);s.pointerMove(4,cross.x,cross.y);check(out.buttons[0]);
        s.forceNeutral();out.neutral();
        System.out.println("Touch state: "+checks+" checks passed");
    }
}
